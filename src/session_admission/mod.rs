//! Opt-in admission for one regular HTTP model pool. The gate is shared by all profiles.
mod gate;
pub(crate) mod rank;
use crate::config::{RouterConfig, RoutingMode};
use axum::{
    body::{Body, Bytes},
    extract::Request,
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Response},
};
use futures_util::StreamExt;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    sync::{Arc, Weak},
    time::Duration,
};
use tokio::{
    sync::{mpsc, OwnedSemaphorePermit, Semaphore},
    time::{timeout_at, Instant},
};
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Settings {
    pub active: usize,
    pub queued_per_session: usize,
    resident_requests: usize,
    resident_bytes: u32,
    pub active_per_subject: Option<usize>,
    trusted_session_header: Option<String>,
    pub rank_capacity: bool,
    resident_per_subject: Option<usize>,
    bytes_per_subject: Option<u32>,
    sessions: Option<usize>,
    sessions_per_subject: Option<usize>,
    #[serde(default)]
    profiles: HashMap<String, String>,
}
impl Settings {
    fn subject_limit(&self) -> usize {
        self.active_per_subject.unwrap_or(self.active)
    }
}

#[derive(Debug)]
pub(crate) struct Admission {
    gate: Arc<gate::Gate>,
    settings: Arc<Settings>,
    requests: Arc<Semaphore>,
    bytes: Arc<Semaphore>,
    subjects: parking_lot::Mutex<HashMap<[u8; 32], Weak<SubjectBudget>>>,
    rank_urls: Arc<Vec<String>>,
    policies: HashMap<String, Arc<dyn crate::policies::LoadBalancingPolicy>>,
    timeout: Duration,
    max_body: usize,
}

#[derive(Debug)]
struct SubjectBudget {
    requests: Arc<Semaphore>,
    bytes: Arc<Semaphore>,
}

#[derive(Clone)]
pub(crate) struct Execution {
    pub ticket: Arc<gate::Ticket>,
    pub hard: bool,
    pub rank_urls: Arc<Vec<String>>,
    pub policy: Option<Arc<dyn crate::policies::LoadBalancingPolicy>>,
    pub deadline: Instant,
}
tokio::task_local! { pub(crate) static EXECUTION: Execution; }

impl Admission {
    pub fn configured(config: &RouterConfig) -> Result<Option<Arc<Self>>, String> {
        let Some(path) = std::env::var_os("VLLM_ROUTER_SESSION_CONFIG") else {
            return Ok(None);
        };
        let bytes = std::fs::read(path).map_err(|e| format!("session configuration: {e}"))?;
        let settings: Settings =
            serde_json::from_slice(&bytes).map_err(|e| format!("session configuration: {e}"))?;
        let supported = matches!(&config.mode, RoutingMode::Regular{worker_urls} if !worker_urls.is_empty() && worker_urls.iter().all(|url|url.starts_with("http://")||url.starts_with("https://")));
        if !supported
            || config.enable_igw
            || config.epd.is_some()
            || config.program_scheduling.is_some()
            || config.discovery.is_some()
        {
            return Err("session admission requires a static regular HTTP pool with exclusive session scheduling".into());
        }
        if settings.rank_capacity && config.intra_node_data_parallel_size > 1 {
            return Err("rank capacity requires independently addressed single-DP workers".into());
        }
        if settings.active == 0
            || settings.subject_limit() == 0
            || settings.queued_per_session == 0
            || settings.resident_requests < settings.active
            || settings.resident_requests > Semaphore::MAX_PERMITS
            || settings.resident_bytes == 0
            || config.request_timeout_secs == 0
        {
            return Err("invalid session admission budgets".into());
        }
        if let Some(h) = &settings.trusted_session_header {
            h.parse::<http::HeaderName>().map_err(|e| e.to_string())?;
        }
        if settings.rank_capacity
            && std::env::var("VLLM_SESSION_RANK_KEY")
                .unwrap_or_default()
                .is_empty()
        {
            return Err("rank capacity requires VLLM_SESSION_RANK_KEY".into());
        }
        if settings
            .resident_per_subject
            .is_some_and(|v| v == 0 || v > settings.resident_requests)
            || settings
                .bytes_per_subject
                .is_some_and(|v| v == 0 || v > settings.resident_bytes)
            || settings.sessions == Some(0)
            || settings.sessions_per_subject == Some(0)
        {
            return Err("invalid per-subject or session budget".into());
        }
        let rank_urls = match &config.mode {
            RoutingMode::Regular { worker_urls } => Arc::new(worker_urls.clone()),
            _ => unreachable!(),
        };
        let primary = config.policy.name();
        if settings.rank_capacity && !matches!(primary, "round_robin" | "cache_aware" | "kv_aware")
        {
            return Err(format!("unsupported capacity-aware policy {primary}"));
        }
        let mut policies = HashMap::new();
        let mut by_name = HashMap::new();
        for (profile, name) in &settings.profiles {
            // The primary policy owns the model registry and its worker subscriptions.
            if name == primary {
                continue;
            }
            if !matches!(
                name.as_str(),
                "cache_aware" | "round_robin" | "random" | "consistent_hash" | "rendezvous_hash"
            ) || (settings.rank_capacity
                && !matches!(name.as_str(), "cache_aware" | "round_robin"))
            {
                return Err(format!("unsupported secondary policy {name}"));
            }
            let policy = by_name
                .entry(name)
                .or_insert_with(|| {
                    crate::policies::PolicyFactory::create_by_name(name)
                        .expect("supported secondary policy")
                })
                .clone();
            policies.insert(profile.clone(), policy);
        }
        let settings = Arc::new(settings);
        Ok(Some(Arc::new(Self {
            gate: gate::Gate::new(settings.clone()),
            requests: Arc::new(Semaphore::new(settings.resident_requests)),
            bytes: Arc::new(Semaphore::new(settings.resident_bytes as usize)),
            settings,
            policies,
            subjects: Default::default(),
            rank_urls,
            timeout: Duration::from_secs(config.request_timeout_secs),
            max_body: config.max_payload_size,
        })))
    }

    pub async fn serve(
        self: &Arc<Self>,
        req: Request,
        next: Next,
        state: &Arc<crate::server::AppState>,
    ) -> Response {
        let deadline = Instant::now() + self.timeout;
        let Ok(slot) = self.requests.clone().try_acquire_owned() else {
            return StatusCode::TOO_MANY_REQUESTS.into_response();
        };
        let result = timeout_at(deadline, self.handle(req, next, state, deadline, slot)).await;
        result.unwrap_or_else(|_| StatusCode::GATEWAY_TIMEOUT.into_response())
    }

    async fn handle(
        &self,
        req: Request,
        next: Next,
        state: &Arc<crate::server::AppState>,
        deadline: Instant,
        slot: OwnedSemaphorePermit,
    ) -> Response {
        if let Err(response) = crate::server::authorize_request(state, req.headers()).await {
            return response;
        }
        let credential = if state.context.api_key_validation_urls.is_empty() {
            ""
        } else {
            req.headers()
                .get(http::header::AUTHORIZATION)
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.strip_prefix("Bearer "))
                .unwrap_or_default()
                .trim()
        };
        let subject: [u8; 32] = Sha256::digest(credential.as_bytes()).into();
        let budgets = {
            let mut subjects = self.subjects.lock();
            subjects.retain(|_, entry| entry.strong_count() > 0);
            if let Some(existing) = subjects.get(&subject).and_then(Weak::upgrade) {
                existing
            } else {
                let budget = Arc::new(SubjectBudget {
                    requests: Arc::new(Semaphore::new(
                        self.settings
                            .resident_per_subject
                            .unwrap_or(self.settings.resident_requests),
                    )),
                    bytes: Arc::new(Semaphore::new(
                        self.settings
                            .bytes_per_subject
                            .unwrap_or(self.settings.resident_bytes)
                            as usize,
                    )),
                });
                subjects.insert(subject, Arc::downgrade(&budget));
                budget
            }
        };
        let Ok(subject_slot) = budgets.requests.clone().try_acquire_owned() else {
            return StatusCode::TOO_MANY_REQUESTS.into_response();
        };
        let (parts, body) = req.into_parts();
        if let Some(value) = parts.headers.get("x-router-profile") {
            if !value
                .to_str()
                .is_ok_and(|name| self.settings.profiles.contains_key(name))
            {
                return (StatusCode::BAD_REQUEST, "unknown router profile").into_response();
            }
        }
        let mut chunks = body.into_data_stream();
        let mut data = bytes::BytesMut::new();
        let mut budget = None::<OwnedSemaphorePermit>;
        let mut subject_bytes = None::<OwnedSemaphorePermit>;
        while let Some(piece) = chunks.next().await {
            let Ok(piece) = piece else {
                return StatusCode::BAD_REQUEST.into_response();
            };
            if piece.len() > self.max_body.saturating_sub(data.len()) {
                return StatusCode::PAYLOAD_TOO_LARGE.into_response();
            }
            let Ok(n) = u32::try_from(piece.len()) else {
                return StatusCode::PAYLOAD_TOO_LARGE.into_response();
            };
            let Ok(extra) = self.bytes.clone().try_acquire_many_owned(n) else {
                return StatusCode::TOO_MANY_REQUESTS.into_response();
            };
            if let Some(b) = &mut budget {
                b.merge(extra)
            } else {
                budget = Some(extra)
            }
            let Ok(extra) = budgets.bytes.clone().try_acquire_many_owned(n) else {
                return StatusCode::TOO_MANY_REQUESTS.into_response();
            };
            if let Some(b) = &mut subject_bytes {
                b.merge(extra)
            } else {
                subject_bytes = Some(extra)
            }
            data.extend_from_slice(&piece);
        }
        let data = data.freeze();
        #[derive(Deserialize)]
        struct SessionParams {
            session_id: Option<String>,
        }
        #[derive(Deserialize)]
        struct Metadata {
            session_params: Option<SessionParams>,
            stream: Option<bool>,
        }
        let meta: Metadata = match serde_json::from_slice(&data) {
            Ok(v) => v,
            Err(e) => return (StatusCode::BAD_REQUEST, e.to_string()).into_response(),
        };
        let body_id = meta.session_params.and_then(|params| params.session_id);
        let header_id = self
            .settings
            .trusted_session_header
            .as_ref()
            .and_then(|h| parts.headers.get(h));
        let header = match header_id.map(|v| v.to_str()).transpose() {
            Ok(v) => v,
            Err(_) => return StatusCode::BAD_REQUEST.into_response(),
        };
        if matches!((&body_id,header),(Some(a),Some(b)) if a!=b) {
            return (StatusCode::BAD_REQUEST, "conflicting session identity").into_response();
        }
        let id = body_id
            .or_else(|| header.map(str::to_owned))
            .unwrap_or_else(|| Uuid::new_v4().to_string());
        if id.trim().is_empty() || id.len() > 256 || id.chars().any(char::is_control) {
            return (StatusCode::BAD_REQUEST, "invalid session identity").into_response();
        }
        let key = gate::SessionKey { subject, id };
        let Some(ticket) = self.gate.enqueue(key) else {
            return StatusCode::TOO_MANY_REQUESTS.into_response();
        };
        tracing::debug!(request=%ticket.id,"session_enqueued");
        ticket.granted().await;
        tracing::debug!(request=%ticket.id,"session_granted");
        let policy = parts
            .headers
            .get("x-router-profile")
            .and_then(|v| v.to_str().ok())
            .and_then(|name| self.policies.get(name))
            .cloned();
        let context = Execution {
            ticket: ticket.clone(),
            rank_urls: self.rank_urls.clone(),
            hard: self.settings.rank_capacity,
            policy,
            deadline,
        };
        let response = EXECUTION
            .scope(
                context,
                next.run(Request::from_parts(parts, Body::from(data))),
            )
            .await;
        if meta.stream != Some(true) || !response.status().is_success() {
            return response;
        }
        let (parts, body) = response.into_parts();
        let (tx, rx) = mpsc::channel::<Result<Bytes, axum::Error>>(1);
        tokio::spawn(async move {
            let _holds = (slot, budget, subject_slot, subject_bytes, budgets, ticket);
            let mut stream = body.into_data_stream();
            let forward = async {
                while let Some(piece) = stream.next().await {
                    if tx.send(piece).await.is_err() {
                        break;
                    }
                }
            };
            tokio::select! {_ = tx.closed()=>{}, _ = tokio::time::sleep_until(deadline)=>{}, _=forward=>{}}
        });
        Response::from_parts(
            parts,
            Body::from_stream(tokio_stream::wrappers::ReceiverStream::new(rx)),
        )
    }
}
