//! Source-aware token-fact resolution and shadow admission accounting.
//!
//! The Router decides *which* token count a Program admission uses; this module
//! owns that policy so the scheduling modules keep a single numeric demand.
//! Resolution is intentionally cheap: one lock, no tokenizer work, and no
//! additional encoding. Prepared counts arrive with the request.

use super::scheduler::ProgramScheduler;
use super::scheduler_state::ProgramSchedulerState;
use super::{ArrivalTokenFacts, ContextTokens, ProgramRef, TokenCountMode, TokenizerFingerprint};
use crate::metrics::RouterMetrics;
use std::time::Instant;
use tracing::warn;

/// Maximum revoked fingerprints retained in scheduler state.
///
/// Revocation is a correctness fallback, not an audit log: the counter keeps
/// the total. The bound only prevents unbounded scheduler memory.
const MAX_REVOKED_FINGERPRINTS: usize = 64;

impl ProgramScheduler {
    /// Record the active token-count mode once at startup.
    pub fn publish_token_count_mode(&self) {
        RouterMetrics::set_agent_aware_context_token_mode(self.config.token_count_mode.as_str());
    }

    /// Whether prepared counts may be applied for this model pool.
    pub(crate) fn prepared_token_pool_allowed(&self, model_pool: &str) -> bool {
        self.config.token_count_model_pools.is_empty()
            || self
                .config
                .token_count_model_pools
                .iter()
                .any(|allowed| allowed == model_pool)
    }

    fn fingerprint_revoked(
        &self,
        state: &ProgramSchedulerState,
        fingerprint: Option<TokenizerFingerprint>,
    ) -> bool {
        fingerprint
            .is_some_and(|fingerprint| state.revoked_token_fingerprints.contains_key(&fingerprint))
    }

    /// Resolve the applied context demand for one arrival.
    ///
    /// In `estimated` mode this returns the legacy byte estimate unchanged. In
    /// `shadow` mode it also records the prepared counterfactual without
    /// changing any decision. In `exact_when_verified` mode it applies the
    /// prepared count unless the model pool is not allowlisted or the
    /// fingerprint was revoked after an Engine disagreement.
    pub fn resolve_arrival_tokens(
        &self,
        model_pool: &str,
        arrival: ArrivalTokenFacts,
    ) -> ContextTokens {
        let allowed = self.prepared_token_pool_allowed(model_pool);
        let revoked = {
            let state = self.state.lock();
            self.fingerprint_revoked(
                &state,
                arrival.prepared().and_then(|facts| facts.fingerprint()),
            )
        };
        super::resolve_context_tokens(self.config.token_count_mode, arrival, allowed, revoked)
    }

    /// Record one arrival-time shadow admission counterfactual.
    ///
    /// The sample answers a single, reproducible question: with the current
    /// ledger on the Program's chosen rank, would the prepared count have been
    /// admitted by the same capacity and Program-count checks that the applied
    /// count uses? It never changes the decision.
    pub(crate) fn record_shadow_admission(
        &self,
        state: &ProgramSchedulerState,
        program: &ProgramRef,
        target_id: &str,
        now: Instant,
    ) {
        let Some(decision) = state.decisions.get(program) else {
            return;
        };
        let Some(shadow_tokens) = decision.shadow_context_tokens else {
            return;
        };
        let capacity = self.config.progress_ttl.token_capacity;
        let output_budget = decision.output_token_reservation;
        let applied_required = decision.private_tokens_with_output(
            self.config.progress_ttl.decode_buffer_tokens,
            output_budget,
        );
        let shadow_required = shadow_tokens
            .saturating_sub(decision.shared_prefix_tokens)
            .saturating_add(
                output_budget
                    .or(decision.output_token_reservation)
                    .unwrap_or(self.config.progress_ttl.decode_buffer_tokens),
            ) as f64;
        let used = self.target_usage(state, target_id, now);
        let count_fits = state
            .runtime
            .iter_views()
            .filter(|other| {
                other.state == super::ProgramState::Active && other.placement == Some(target_id)
            })
            .count()
            .saturating_add(1)
            <= self.config.max_active_programs_per_target;
        let (applied_fits, shadow_fits) = match capacity {
            Some(capacity) => {
                let low = capacity as f64 * self.config.progress_ttl.low_watermark_ratio;
                (
                    count_fits && used + applied_required <= low,
                    count_fits && used + shadow_required <= low,
                )
            }
            None => (count_fits, count_fits),
        };
        let outcome = match (applied_fits, shadow_fits) {
            (true, true) => "both_admit",
            (false, false) => "both_deny",
            (true, false) => "estimate_admits_exact_denies",
            (false, true) => "estimate_denies_exact_admits",
        };
        RouterMetrics::record_agent_aware_context_token_shadow(target_id, outcome);
    }

    /// Compare one prepared arrival count with the Engine-observed count.
    ///
    /// A disagreement revokes the fingerprint so later requests for the same
    /// model/template/options return to estimation instead of repeating an
    /// equivalence error. The comparison is bounded and never panics.
    pub(crate) fn reconcile_observed_prompt_tokens(
        &self,
        state: &mut ProgramSchedulerState,
        model_pool: &str,
        source: super::TokenFactSource,
        fingerprint: Option<TokenizerFingerprint>,
        prepared_tokens: Option<usize>,
        observed_tokens: Option<usize>,
    ) {
        let (Some(prepared), Some(observed)) = (prepared_tokens, observed_tokens) else {
            return;
        };
        if !source.is_prepared() || observed == 0 {
            return;
        }
        RouterMetrics::record_agent_aware_context_token_observation_delta(
            model_pool,
            source.as_str(),
            observed as i64 - prepared as i64,
            observed,
        );
        if prepared == observed {
            return;
        }
        let Some(fingerprint) = fingerprint else {
            return;
        };
        if state
            .revoked_token_fingerprints
            .insert(fingerprint, Instant::now())
            .is_none()
        {
            state.token_fingerprint_revocations =
                state.token_fingerprint_revocations.saturating_add(1);
            RouterMetrics::record_agent_aware_context_token_revocation("usage_mismatch");
            warn!(
                event = "token_fact_fingerprint_revoked",
                model_pool,
                source = source.as_str(),
                fingerprint = %fingerprint,
                prepared_tokens,
                observed_tokens,
                "prepared input-token count disagreed with Engine usage; \
                 falling back to estimation for this fingerprint"
            );
        }
        while state.revoked_token_fingerprints.len() > MAX_REVOKED_FINGERPRINTS {
            let oldest = state
                .revoked_token_fingerprints
                .iter()
                .min_by_key(|(_, revoked_at)| **revoked_at)
                .map(|(fingerprint, _)| *fingerprint);
            let Some(oldest) = oldest else {
                break;
            };
            state.revoked_token_fingerprints.remove(&oldest);
        }
    }

    /// HTTP metrics endpoint overrides configured for Program targets.
    pub fn metrics_base_urls(&self) -> std::collections::BTreeMap<String, String> {
        self.config.metrics_base_urls.clone()
    }

    /// Number of fingerprints currently revoked, for diagnostics.
    pub fn revoked_token_fingerprint_count(&self) -> usize {
        self.state.lock().token_fingerprint_revocations as usize
    }
}

/// Token-count mode of this scheduler, for diagnostics and startup logging.
impl ProgramScheduler {
    pub fn token_count_mode(&self) -> TokenCountMode {
        self.config.token_count_mode
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::program_scheduling::{
        ProgramSchedulerConfig, ProgramTarget, TokenFactSource, TokenFacts,
    };
    use serde_json::json;

    fn target() -> ProgramTarget {
        ProgramTarget {
            id: "rank-0".into(),
            base_url: "http://rank-0".into(),
            dp_rank: Some(0),
        }
    }

    fn identity() -> super::super::ProgramIdentity {
        super::super::ProgramIdentity::from_request(
            None,
            Some(&json!({
                "vllm_xargs": {"agentic_context": {
                    "program_id": "p",
                    "expected_resume": true
                }}
            })),
            Some("model"),
        )
        .unwrap()
        .unwrap()
    }

    #[tokio::test]
    async fn exact_mode_admits_with_engine_scale_demand() {
        let mut config = ProgramSchedulerConfig {
            token_count_mode: TokenCountMode::ExactWhenVerified,
            ..ProgramSchedulerConfig::default()
        };
        config.progress_ttl.token_capacity = Some(100_000);
        let scheduler = ProgramScheduler::new(config);
        let fingerprint = TokenizerFingerprint::from_parts(&["model", "client"]);
        let context = scheduler.resolve_arrival_tokens(
            "model",
            ArrivalTokenFacts::new(4, Some(TokenFacts::client_token_ids(98_304, fingerprint))),
        );
        assert_eq!(context.tokens(), 98_304);
        assert_eq!(context.source(), TokenFactSource::ClientTokenIds);
        let dispatch = scheduler
            .acquire(identity(), context, std::slice::from_ref(&target()), None)
            .await
            .unwrap();
        assert_eq!(dispatch.context_tokens(), 98_304);
        assert_eq!(
            dispatch.context_token_source(),
            TokenFactSource::ClientTokenIds
        );
    }

    #[tokio::test]
    async fn revocation_falls_back_to_estimation() {
        let mut config = ProgramSchedulerConfig {
            token_count_mode: TokenCountMode::ExactWhenVerified,
            ..ProgramSchedulerConfig::default()
        };
        config.progress_ttl.token_capacity = Some(100_000);
        let scheduler = ProgramScheduler::new(config);
        let fingerprint = TokenizerFingerprint::from_parts(&["model", "client"]);
        {
            let mut state = scheduler.state.lock();
            scheduler.reconcile_observed_prompt_tokens(
                &mut state,
                "model",
                TokenFactSource::ClientTokenIds,
                Some(fingerprint),
                Some(98_304),
                Some(98_300),
            );
            assert_eq!(state.revoked_token_fingerprints.len(), 1);
        }
        let context = scheduler.resolve_arrival_tokens(
            "model",
            ArrivalTokenFacts::new(4, Some(TokenFacts::client_token_ids(98_304, fingerprint))),
        );
        assert_eq!(context.tokens(), 4);
        assert_eq!(
            context.fallback(),
            Some(super::super::TokenFactFallback::FingerprintRevoked)
        );
    }
}
