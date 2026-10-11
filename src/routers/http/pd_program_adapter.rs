//! Opt-in, one-way PD Program scheduling and response-lifetime settlement.
use super::dp_utils;
use super::program_adapter::ProgramCompletion;
use super::vllm_service_discovery::{ServiceRegistry, ServiceType};
use crate::core::{Worker, WorkerRegistry};
use crate::kv_index::{HashMode, KvIndexSupervisor};
use crate::program_scheduling::{
    BackendObservationProvider, ProgramIdentity, ProgramScheduler, ProgramSchedulerConfig,
    ProgramTarget, ScheduleError, VllmMetricsObservationProvider,
};
use crate::server::AppContext;
use crate::token_estimator::{MomentumTokenEstimator, TokenEstimateScope};
use axum::{
    body::Body,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use bytes::Bytes;
use futures_util::{Stream, StreamExt};
use parking_lot::Mutex;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, HashMap},
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
    time::Duration,
};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};
use tracing::warn;

#[derive(Debug)]
pub(super) struct PdProgramRuntime {
    pub scheduler: Arc<ProgramScheduler>,
    estimator: Arc<MomentumTokenEstimator>,
    prefill_gates: Arc<Mutex<HashMap<String, Arc<Semaphore>>>>,
    prefill_limit: usize,
    queue_timeout: Duration,
    observer: tokio::task::AbortHandle,
    kv_observer: Option<tokio::task::AbortHandle>,
}

impl PdProgramRuntime {
    pub fn new(
        ctx: &Arc<AppContext>,
        services: Arc<ServiceRegistry>,
        discovery: bool,
    ) -> Result<Option<Self>, String> {
        let Some(config) = ctx.router_config.program_scheduling.as_ref() else {
            return Ok(None);
        };
        if !config.pd.enabled {
            return Ok(None);
        }
        let scheduler = Arc::new(ProgramScheduler::new(ProgramSchedulerConfig::from(config)));
        let observation_scheduler = scheduler.clone();
        let workers = ctx.worker_registry.clone();
        let dp_size = ctx.router_config.intra_node_data_parallel_size;
        let provider = VllmMetricsObservationProvider::new(
            ctx.client.clone(),
            ctx.router_config.api_key.clone(),
        );
        let kv_config = config.pd.clone();
        let mut default_headers = HeaderMap::new();
        if let Some(key) = &ctx.router_config.api_key {
            if let Ok(value) = format!("Bearer {key}").parse() {
                default_headers.insert(axum::http::header::AUTHORIZATION, value);
            }
        }
        let kv_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .default_headers(default_headers)
            .build()
            .map_err(|error| format!("PD KV client initialization failed: {error}"))?;
        let kv_workers_registry = workers.clone();
        let kv_services = services.clone();
        let kv_interval = scheduler.metrics_interval().max(Duration::from_secs(1));
        let kv_observer = if kv_config.kv_events {
            Some(
                tokio::spawn(async move {
                    let (supervisor, mut signals) = KvIndexSupervisor::new(
                        kv_client,
                        HashMode::parse_config(&kv_config.kv_hash_mode),
                    );
                    let mut previous = BTreeSet::new();
                    let mut interval = tokio::time::interval(kv_interval);
                    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                    loop {
                        interval.tick().await;
                        while signals.try_recv().is_ok() {}
                        let urls = all_worker_bases(&kv_workers_registry, &kv_services, discovery);
                        for removed in previous.difference(&urls) {
                            supervisor.on_worker_removed(removed);
                        }
                        for added in urls.difference(&previous) {
                            if let Err(error) = supervisor.on_worker_added(added).await {
                                warn!(%error, "PD KV discovery failed");
                            }
                        }
                        previous = urls;
                    }
                })
                .abort_handle(),
            )
        } else {
            None
        };
        let prefill_gates = Arc::new(Mutex::new(HashMap::<String, Arc<Semaphore>>::new()));
        let observer_gates = prefill_gates.clone();
        let prefill_limit = config.pd.max_prefill_requests_per_target;
        let observer = tokio::spawn(async move {
            let mut interval = tokio::time::interval(observation_scheduler.metrics_interval());
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                interval.tick().await;
                let targets = decode_targets(&workers, &services, discovery, dp_size);
                for pool in observation_scheduler.model_pools() {
                    observation_scheduler.sync_targets(&pool, &targets);
                }
                let epoch = observation_scheduler.begin_observation(&targets);
                let (observations, failures) = provider.observe(&targets).await;
                for failure in failures {
                    warn!(error = %failure.error, "PD metrics scrape failed");
                }
                observation_scheduler.apply_observations(epoch, observations);
                observation_scheduler.tick();
                let live_prefill: BTreeSet<String> = if discovery {
                    services
                        .get_prefill_instances()
                        .iter()
                        .map(|(url, _)| discovered_target(url).id)
                        .collect()
                } else {
                    workers
                        .get_prefill_workers()
                        .iter()
                        .map(|w| w.url().to_string())
                        .collect()
                };
                observer_gates.lock().retain(|id, gate| {
                    live_prefill.contains(id)
                        || Arc::strong_count(gate) > 1
                        || gate.available_permits() != prefill_limit
                });
            }
        })
        .abort_handle();
        Ok(Some(Self {
            scheduler,
            estimator: Arc::new(MomentumTokenEstimator::default()),
            prefill_gates,
            prefill_limit: config.pd.max_prefill_requests_per_target,
            queue_timeout: Duration::from_secs_f64(config.queue_timeout_seconds),
            observer,
            kv_observer,
        }))
    }

    pub async fn acquire(
        &self,
        headers: Option<&HeaderMap>,
        request: &Value,
        path: &str,
        targets: &[ProgramTarget],
    ) -> Result<Option<ProgramCompletion>, ScheduleError> {
        let pool = model_pool(headers, request);
        let Some(mut identity) = ProgramIdentity::from_request_with_enable_key(
            headers,
            Some(request),
            Some(&pool),
            self.scheduler.enable_key(),
        )?
        else {
            return Ok(None);
        };
        // The whole request includes templates, tools and multimodal envelopes.
        // HTTP token estimation is advisory; engine physical limits remain authoritative.
        let (tokens, calibration) = if let Some(tokens) = explicit_completion_tokens(request, path)
        {
            // A flat token-ID prompt is already tokenized. Counting its JSON
            // digits over-reserves admission and can strand first-turn waiters.
            // Empty calibration input keeps this exact count from changing the
            // byte estimator used for text/chat requests in the same pool.
            let (_, calibration) = self
                .estimator
                .estimate(TokenEstimateScope::new(&pool, path), "");
            (tokens, calibration)
        } else {
            let input = serde_json::to_string(request).unwrap_or_default();
            self.estimator
                .estimate(TokenEstimateScope::new(&pool, path), &input)
        };
        let wire_limit = request
            .get("max_completion_tokens")
            .or_else(|| request.get("max_tokens"))
            .and_then(Value::as_u64)
            .and_then(|n| usize::try_from(n).ok());
        // Without a wire limit, reserve the configured decode buffer. Engines
        // still enforce their own physical limits; HTTP estimation is advisory.
        if let Some(limit) = wire_limit {
            identity.reserve_pd_output(limit);
        }
        let dispatch = self
            .scheduler
            .acquire(identity, tokens, targets, None)
            .await?;
        Ok(Some(ProgramCompletion::new(
            self.scheduler.clone(),
            dispatch,
            self.estimator.clone(),
            calibration,
        )))
    }

    pub async fn reserve_prefill(
        &self,
        worker_id: &str,
    ) -> Result<OwnedSemaphorePermit, ScheduleError> {
        let gate = self
            .prefill_gates
            .lock()
            .entry(worker_id.to_string())
            .or_insert_with(|| Arc::new(Semaphore::new(self.prefill_limit)))
            .clone();
        tokio::time::timeout(self.queue_timeout, gate.acquire_owned())
            .await
            .map_err(|_| ScheduleError::QueueTimeout)?
            .map_err(|_| ScheduleError::NoTargets)
    }
}

fn explicit_completion_tokens(request: &Value, path: &str) -> Option<usize> {
    if path != "/v1/completions" {
        return None;
    }
    let tokens = request.get("prompt")?.as_array()?;
    (!tokens.is_empty()
        && tokens
            .iter()
            .all(|token| token.as_u64().is_some_and(|id| id <= u32::MAX as u64)))
    .then_some(tokens.len())
}

impl Drop for PdProgramRuntime {
    fn drop(&mut self) {
        self.observer.abort();
        if let Some(observer) = &self.kv_observer {
            observer.abort();
        }
    }
}

pub(super) fn model_pool(headers: Option<&HeaderMap>, request: &Value) -> String {
    // Isolate credentials without retaining or logging them. With gateway auth,
    // deploy one authoritative Router shard per authenticated tenant/pool.
    let principal = headers
        .and_then(|h| h.get(axum::http::header::AUTHORIZATION))
        .map(|v| v.as_bytes())
        .unwrap_or_default();
    let model = request
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or("default");
    let mut digest = Sha256::new();
    digest.update((principal.len() as u64).to_le_bytes());
    digest.update(principal);
    digest.update(model.as_bytes());
    format!("pd:{:x}", digest.finalize())
}

pub(super) fn worker_target(worker: &Arc<dyn Worker>) -> ProgramTarget {
    let (base_url, rank) = dp_utils::parse_worker_url(worker.url());
    ProgramTarget {
        id: worker.url().to_string(),
        base_url,
        dp_rank: worker.dp_rank().or(rank),
    }
}

pub(super) fn discovered_target(address: &str) -> ProgramTarget {
    let url = if address.starts_with("http://") || address.starts_with("https://") {
        address.to_string()
    } else {
        format!("http://{address}")
    };
    let (base_url, dp_rank) = dp_utils::parse_worker_url(&url);
    ProgramTarget {
        id: url,
        base_url,
        dp_rank,
    }
}

/// Explicit rank selection keeps discovered internal DP D affinity independent of P.
pub(super) fn discovered_decode_instances(
    services: &ServiceRegistry,
    configured_dp: usize,
) -> Vec<(String, String)> {
    services
        .get_decode_instances()
        .into_iter()
        .flat_map(|(address, zmq)| {
            let (_, rank) = dp_utils::parse_worker_url(&address);
            let (_, reported_dp) = services.get_tp_dp_size(&address, ServiceType::Decode);
            let size = reported_dp.max(configured_dp).max(1);
            if rank.is_some() || size == 1 {
                vec![(address, zmq)]
            } else {
                (0..size)
                    .map(|rank| (format!("{address}@{rank}"), zmq.clone()))
                    .collect()
            }
        })
        .collect()
}

fn decode_targets(
    workers: &WorkerRegistry,
    services: &ServiceRegistry,
    discovery: bool,
    dp_size: usize,
) -> Vec<ProgramTarget> {
    if discovery {
        discovered_decode_instances(services, dp_size)
            .iter()
            .map(|(url, _)| discovered_target(url))
            .collect()
    } else {
        workers
            .get_decode_workers()
            .iter()
            .filter(|w| w.is_available())
            .map(worker_target)
            .collect()
    }
}

fn all_worker_bases(
    workers: &WorkerRegistry,
    services: &ServiceRegistry,
    discovery: bool,
) -> BTreeSet<String> {
    if discovery {
        services
            .get_prefill_instances()
            .into_iter()
            .chain(services.get_decode_instances())
            .map(|(url, _)| discovered_target(&url).base_url)
            .collect()
    } else {
        workers
            .get_prefill_workers()
            .into_iter()
            .chain(workers.get_decode_workers())
            .filter(|w| w.is_available())
            .map(|w| worker_target(&w).base_url)
            .collect()
    }
}

pub(super) fn error_response(error: ScheduleError) -> Response {
    let status = match error {
        ScheduleError::InvalidIdentity(_) => StatusCode::BAD_REQUEST,
        ScheduleError::NoTargets => StatusCode::SERVICE_UNAVAILABLE,
        ScheduleError::QueueTimeout => StatusCode::TOO_MANY_REQUESTS,
    };
    (status, error.to_string()).into_response()
}

/// Transparently pass every byte; completion follows the entire D body, not P
/// completion, headers, or the first output. Dropping it settles failure once.
pub(super) fn track_response(
    response: Response,
    completion: ProgramCompletion,
    streaming: bool,
) -> Response {
    let success_status = response.status().is_success();
    let (parts, body) = response.into_parts();
    Response::from_parts(
        parts,
        Body::from_stream(PdTrackedBody {
            inner: Box::pin(body.into_data_stream()),
            completion,
            streaming,
            success_status,
            terminal: false,
            failed: false,
            buffer_overflow: false,
            buffer: Vec::new(),
        }),
    )
}

struct PdTrackedBody {
    inner: Pin<Box<dyn Stream<Item = Result<Bytes, axum::Error>> + Send>>,
    completion: ProgramCompletion,
    streaming: bool,
    success_status: bool,
    terminal: bool,
    failed: bool,
    buffer_overflow: bool,
    buffer: Vec<u8>,
}

impl PdTrackedBody {
    const MAX_BUFFER: usize = 1024 * 1024;
    fn observe(&mut self, bytes: &[u8]) {
        if self.streaming {
            self.completion.observe_sse_chunk(bytes);
            for segment in bytes.split_inclusive(|b| *b == b'\n') {
                if self.buffer.len().saturating_add(segment.len()) > Self::MAX_BUFFER {
                    self.failed = true;
                    self.buffer.clear();
                    continue;
                }
                self.buffer.extend_from_slice(segment);
                if segment.last() == Some(&b'\n') {
                    let line = std::mem::take(&mut self.buffer);
                    self.observe_line(&line);
                }
            }
        } else if !self.buffer_overflow {
            if self.buffer.len().saturating_add(bytes.len()) > Self::MAX_BUFFER {
                self.buffer_overflow = true;
                self.buffer.clear();
            } else {
                self.buffer.extend_from_slice(bytes);
            }
        }
    }
    fn observe_line(&mut self, line: &[u8]) {
        if let Ok(line) = std::str::from_utf8(line) {
            if let Some(data) = line.trim().strip_prefix("data:") {
                let data = data.trim();
                if data == "[DONE]" {
                    self.terminal = true;
                }
                if serde_json::from_str::<Value>(data)
                    .ok()
                    .is_some_and(|v| v.get("error").is_some())
                {
                    self.failed = true;
                }
            }
        }
    }
}

impl Drop for PdTrackedBody {
    fn drop(&mut self) {
        self.completion.finish(false);
    }
}

impl Stream for PdTrackedBody {
    type Item = Result<Bytes, axum::Error>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        match self.inner.poll_next_unpin(cx) {
            Poll::Ready(Some(Ok(bytes))) => {
                self.observe(&bytes);
                Poll::Ready(Some(Ok(bytes)))
            }
            Poll::Ready(Some(Err(error))) => {
                self.failed = true;
                self.completion.finish(false);
                Poll::Ready(Some(Err(error)))
            }
            Poll::Ready(None) => {
                if self.streaming {
                    let tail = std::mem::take(&mut self.buffer);
                    self.observe_line(&tail);
                } else {
                    self.completion.observe_json(&self.buffer);
                }
                self.completion.finish(
                    self.success_status && !self.failed && (!self.streaming || self.terminal),
                );
                Poll::Ready(None)
            }
            Poll::Pending => Poll::Pending,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::program_scheduling::ProgramSchedulerConfig;
    use serde_json::json;

    #[test]
    fn token_id_prompts_count_tokens_without_json_digit_overhead() {
        assert_eq!(
            explicit_completion_tokens(&json!({"prompt":[1,100000,248319]}), "/v1/completions"),
            Some(3)
        );
        for prompt in [
            json!("text"),
            json!([]),
            json!([[1, 2], [3]]),
            json!([1, -2]),
            json!([1, 1.5]),
            json!([4294967296u64]),
        ] {
            assert_eq!(
                explicit_completion_tokens(&json!({"prompt":prompt}), "/v1/completions"),
                None
            );
        }
        assert_eq!(
            explicit_completion_tokens(&json!({"prompt":[1,2]}), "/v1/chat/completions"),
            None
        );
    }

    async fn completion() -> (Arc<ProgramScheduler>, ProgramCompletion) {
        let scheduler = Arc::new(ProgramScheduler::new(ProgramSchedulerConfig::default()));
        let request = json!({"vllm_xargs":{"agentic_context":{
            "program_id":"body-test","expected_resume":true,"kv_retention_ttl_seconds":2.0}}});
        let identity = ProgramIdentity::from_request(None, Some(&request), Some("test"))
            .unwrap()
            .unwrap();
        let target = ProgramTarget {
            id: "d@1".into(),
            base_url: "http://d".into(),
            dp_rank: Some(1),
        };
        let dispatch = scheduler
            .acquire(identity, 100, &[target], None)
            .await
            .unwrap();
        let estimator = Arc::new(MomentumTokenEstimator::default());
        let (_, calibration) =
            estimator.estimate(TokenEstimateScope::new("test", "/chat"), "hello");
        let completion =
            ProgramCompletion::new(scheduler.clone(), dispatch, estimator, calibration);
        (scheduler, completion)
    }

    #[test]
    fn model_and_credentials_isolate_identical_program_ids() {
        let mut a = HeaderMap::new();
        a.insert("authorization", "Bearer tenant-a".parse().unwrap());
        let mut b = HeaderMap::new();
        b.insert("authorization", "Bearer tenant-b".parse().unwrap());
        assert_ne!(
            model_pool(Some(&a), &json!({"model":"m"})),
            model_pool(Some(&b), &json!({"model":"m"}))
        );
        assert_ne!(
            model_pool(Some(&a), &json!({"model":"m"})),
            model_pool(Some(&a), &json!({"model":"m2"}))
        );
        assert!(!model_pool(Some(&a), &json!({"model":"m"})).contains("tenant-a"));
    }

    #[tokio::test]
    async fn sse_bytes_headers_and_usage_survive_fragmented_terminal() {
        let (scheduler, completion) = completion().await;
        let chunks = [
            "data: {\"usage\":{\"prompt_tokens\":120,\"completion_tokens\":7}}\n\n",
            "data: [DO",
            "NE]\n\n",
        ];
        let input = chunks.concat();
        let response = Response::builder()
            .header("x-backend", "preserved")
            .body(Body::from_stream(futures_util::stream::iter(
                chunks
                    .into_iter()
                    .map(|s| Ok::<_, std::io::Error>(Bytes::from(s))),
            )))
            .unwrap();
        let response = track_response(response, completion, true);
        assert_eq!(
            scheduler.diagnostics().ranks[0].programs[0].in_flight_requests,
            1
        );
        assert_eq!(response.headers()["x-backend"], "preserved");
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        assert_eq!(bytes.as_ref(), input.as_bytes());
        let program = &scheduler.diagnostics().ranks[0].programs[0];
        assert_eq!(program.in_flight_requests, 0);
        assert_eq!(program.estimated_context_tokens, 120);
        assert_eq!(program.state, "active");
    }

    #[tokio::test]
    async fn partial_sse_eof_and_error_events_are_failures() {
        for data in [
            "data: {\"choices\":[]}\n\n",
            "data: {\"error\":\"backend failed\"}\n\ndata: [DONE]\n\n",
        ] {
            let (scheduler, completion) = completion().await;
            let response = track_response(Response::new(Body::from(data)), completion, true);
            axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
            assert_eq!(scheduler.diagnostics().ranks[0].programs[0].state, "paused");
            assert_eq!(
                scheduler.diagnostics().ranks[0].programs[0].in_flight_requests,
                0
            );
        }
    }

    #[tokio::test]
    async fn body_drop_settles_once_and_late_finish_cannot_reverse_failure() {
        let (scheduler, completion) = completion().await;
        let late = completion.clone();
        let response = track_response(
            Response::new(Body::from("data: [DONE]\n\n")),
            completion,
            true,
        );
        drop(response);
        // The final completion owner is a clone held here: release it to trigger Drop.
        late.finish(false);
        late.finish(true);
        assert_eq!(scheduler.diagnostics().ranks[0].programs[0].state, "paused");
        assert_eq!(
            scheduler.diagnostics().ranks[0].programs[0].in_flight_requests,
            0
        );
    }

    #[tokio::test]
    async fn json_body_success_and_error_status_settle_after_eof() {
        for status in [StatusCode::OK, StatusCode::BAD_GATEWAY] {
            let (scheduler, completion) = completion().await;
            let response = track_response(
                Response::builder()
                    .status(status)
                    .body(Body::from(
                        r#"{"usage":{"prompt_tokens":110,"completion_tokens":8}}"#,
                    ))
                    .unwrap(),
                completion,
                false,
            );
            axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
            let snapshot = scheduler.diagnostics();
            assert_eq!(snapshot.ranks[0].programs[0].in_flight_requests, 0);
            assert_eq!(
                snapshot.ranks[0].programs[0].state,
                if status.is_success() {
                    "active"
                } else {
                    "paused"
                }
            );
        }
    }
}
