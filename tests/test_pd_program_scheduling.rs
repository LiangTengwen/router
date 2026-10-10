//! End-to-end HTTP PD tests: two turns, actual rank headers, opt-out and body drop.
use axum::{
    extract::{Json, State},
    http::HeaderMap,
    routing::{get, post},
    Router,
};
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use vllm_router_rs::{
    config::{ConfigValidator, PolicyConfig, ProgramSchedulingConfig, RouterConfig, RoutingMode},
    routers::{http::vllm_pd_router::VllmPDRouter, RouterTrait},
    server::AppContext,
};

#[derive(Clone)]
struct MockState {
    prefill: bool,
    calls: Arc<Mutex<Vec<(String, Value)>>>,
}
struct Mock {
    url: String,
    calls: Arc<Mutex<Vec<(String, Value)>>>,
    task: tokio::task::JoinHandle<()>,
}
impl Drop for Mock {
    fn drop(&mut self) {
        self.task.abort();
    }
}
async fn mock(prefill: bool) -> Mock {
    let calls = Arc::new(Mutex::new(Vec::new()));
    let state = MockState {
        prefill,
        calls: calls.clone(),
    };
    let app = Router::new().route("/health", get(|| async { "ok" }))
        .route("/get_server_info", get(|| async { Json(json!({"model_id":"test","data_parallel_size":2})) }))
        .route("/v1/models", get(|| async { Json(json!({"data":[{"id":"test"}]})) }))
        .route("/metrics", get(|| async {
            "vllm:kv_cache_usage_perc{engine=\"0\"} 0\nvllm:num_requests_running{engine=\"0\"} 0\nvllm:num_requests_waiting{engine=\"0\"} 0\nvllm:kv_cache_usage_perc{engine=\"1\"} 0\nvllm:num_requests_running{engine=\"1\"} 0\nvllm:num_requests_waiting{engine=\"1\"} 0\n"
        }))
        .route("/v1/completions", post(generate))
        .route("/v1/chat/completions", post(generate)).with_state(state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    Mock { url, calls, task }
}
async fn generate(
    State(state): State<MockState>,
    headers: HeaderMap,
    Json(request): Json<Value>,
) -> Json<Value> {
    let rank = headers
        .get("x-data-parallel-rank")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("none")
        .to_string();
    state.calls.lock().unwrap().push((rank, request));
    if state.prefill {
        Json(
            json!({"kv_transfer_params":{"do_remote_prefill":true,"do_remote_decode":false,
            "remote_engine_id":"registered-prefill","remote_block_ids":[1]},
            "choices":[],"usage":{"prompt_tokens":24,"completion_tokens":1}}),
        )
    } else {
        Json(json!({"id":"mock","choices":[{"text":"done"}],
            "usage":{"prompt_tokens":24,"completion_tokens":4,"prompt_tokens_details":{"cached_tokens":24}}}))
    }
}
async fn router(p: &Mock, d: &Mock, enabled: bool) -> VllmPDRouter {
    let mut config = RouterConfig::default();
    config.mode = RoutingMode::VllmPrefillDecode {
        prefill_urls: vec![(p.url.clone(), None)],
        decode_urls: vec![d.url.clone()],
        prefill_policy: Some(PolicyConfig::RoundRobin),
        decode_policy: Some(PolicyConfig::RoundRobin),
        discovery_address: None,
    };
    config.intra_node_data_parallel_size = 2;
    config.worker_startup_timeout_secs = 2;
    config.disable_circuit_breaker = true;
    if enabled {
        config.program_scheduling = Some(
            serde_json::from_value(json!({
                "token_capacity_per_dp_rank":100000,"enable_batch_gain_admission":false,
                "metrics_interval_seconds":0.02,"queue_timeout_seconds":2,"force_resume_timeout_seconds":1,
                "pd":{"enabled":true,"explicit_ttl":true}
            }))
            .unwrap(),
        );
    }
    ConfigValidator::validate(&config).unwrap();
    let ctx = Arc::new(AppContext::new(config, reqwest::Client::new(), 64, None, vec![]).unwrap());
    ctx.policy_registry.set_prefill_policy(
        vllm_router_rs::policies::PolicyFactory::create_from_config(&PolicyConfig::RoundRobin),
    );
    ctx.policy_registry.set_decode_policy(
        vllm_router_rs::policies::PolicyFactory::create_from_config(&PolicyConfig::RoundRobin),
    );
    VllmPDRouter::new(vec![(p.url.clone(), None)], vec![d.url.clone()], None, &ctx)
        .await
        .unwrap()
}
fn request(program: bool) -> vllm_router_rs::protocols::spec::CompletionRequest {
    let mut request = json!({"model":"test","prompt":"hello","max_tokens":8});
    if program {
        request["vllm_xargs"] = json!({"agentic_context":{"program_id":"agent-task",
        "expected_resume":true,"kv_retention_ttl_seconds":1.0}});
    }
    serde_json::from_value(request).unwrap()
}

#[tokio::test]
async fn program_keeps_actual_decode_rank_and_selects_prefill_each_turn() {
    let p = mock(true).await;
    let d = mock(false).await;
    let router = router(&p, &d, true).await;
    for _ in 0..2 {
        let response = router
            .route_completion(None, &request(true), Some("test"))
            .await;
        assert!(response.status().is_success());
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let value: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(value["choices"][0]["text"], "done");
    }
    let pc = p.calls.lock().unwrap();
    let dc = d.calls.lock().unwrap();
    assert_eq!(pc.len(), 2);
    assert_eq!(dc.len(), 2);
    assert_ne!(pc[0].0, pc[1].0, "P policy runs per turn");
    assert_eq!(dc[0].0, dc[1].0, "D rank is bound across turns");
    assert_ne!(dc[0].0, "none");
    assert_eq!(pc[0].1["max_tokens"], 1);
    assert_eq!(dc[0].1["max_tokens"], 8);
    assert_eq!(
        dc[1].1["kv_transfer_params"]["remote_engine_id"],
        "registered-prefill"
    );
}

#[tokio::test]
async fn no_identity_and_disabled_feature_keep_native_round_robin() {
    for (enabled, program) in [(true, false), (false, true)] {
        let p = mock(true).await;
        let d = mock(false).await;
        let router = router(&p, &d, enabled).await;
        for _ in 0..2 {
            let response = router
                .route_completion(None, &request(program), Some("test"))
                .await;
            assert!(response.status().is_success());
            axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
        }
        let dc = d.calls.lock().unwrap();
        assert_eq!(dc.len(), 2);
        assert_ne!(dc[0].0, dc[1].0, "native D policy still runs per request");
    }
}

#[test]
fn pd_requires_explicit_capacity_and_rejects_unverified_modes() {
    let valid: ProgramSchedulingConfig =
        serde_json::from_value(json!({"pd":{"enabled":true}})).unwrap();
    assert!(valid.pd.enabled);
    let mut config = RouterConfig::default();
    config.mode = RoutingMode::VllmPrefillDecode {
        prefill_urls: vec![("http://p:8000".into(), None)],
        decode_urls: vec!["http://d:8000".into()],
        prefill_policy: None,
        decode_policy: None,
        discovery_address: None,
    };
    config.program_scheduling = Some(valid);
    assert!(ConfigValidator::validate(&config).is_err());
    let program = config.program_scheduling.as_mut().unwrap();
    program.token_capacity_per_dp_rank = Some(100000);
    program.enable_batch_gain_admission = false;
    assert!(ConfigValidator::validate(&config).is_ok());
    for mutation in 0..4 {
        let mut bad = config.clone();
        let program = bad.program_scheduling.as_mut().unwrap();
        match mutation {
            0 => program.binding_only = true,
            1 => program.enable_batch_gain_admission = true,
            2 => program.pd.max_prefill_requests_per_target = 0,
            _ => program.pd.kv_hash_mode = "unknown".into(),
        }
        assert!(ConfigValidator::validate(&bad).is_err());
    }
    assert!(serde_json::from_value::<ProgramSchedulingConfig>(
        json!({"pd":{"enabled":true,"local_append":true}})
    )
    .is_err());
    assert!(serde_json::from_value::<ProgramSchedulingConfig>(
        json!({"pd":{"enabled":true,"bidirectional_multiturn":true}})
    )
    .is_err());
}

#[tokio::test]
async fn dropping_decode_body_unblocks_only_the_waiting_turn() {
    let p = mock(true).await;
    let d = mock(false).await;
    let router = Arc::new(router(&p, &d, true).await);
    let first = router
        .route_completion(None, &request(true), Some("test"))
        .await;
    assert!(first.status().is_success());
    let second_router = router.clone();
    let mut second = tokio::spawn(async move {
        second_router
            .route_completion(None, &request(true), Some("test"))
            .await
    });
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(20), &mut second)
            .await
            .is_err()
    );
    assert_eq!(p.calls.lock().unwrap().len(), 1);
    drop(first);
    let second = tokio::time::timeout(std::time::Duration::from_secs(2), second)
        .await
        .unwrap()
        .unwrap();
    assert!(second.status().is_success());
    axum::body::to_bytes(second.into_body(), usize::MAX)
        .await
        .unwrap();
    assert_eq!(p.calls.lock().unwrap().len(), 2);
    let diagnostic = router.get_worker_loads().await;
    let bytes = axum::body::to_bytes(diagnostic.into_body(), usize::MAX)
        .await
        .unwrap();
    let value: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(value["program_scheduling"]["warm_enabled"], false);
    let programs = value["program_scheduling"]["scheduler"]["ranks"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|r| r["programs"].as_array().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(programs.len(), 1);
    assert_eq!(programs[0]["in_flight_requests"], 0);
    assert_eq!(programs[0]["waiting_requests"], 0);
}

#[tokio::test]
async fn chat_program_turns_use_the_same_decode_rank() {
    let p = mock(true).await;
    let d = mock(false).await;
    let router = router(&p, &d, true).await;
    let request = serde_json::from_value(
        json!({"model":"test","messages":[{"role":"user","content":"hello"}],
        "max_tokens":8,"vllm_xargs":{"agentic_context":{"program_id":"chat-agent",
        "expected_resume":true,"kv_retention_ttl_seconds":1.0}}}),
    )
    .unwrap();
    for _ in 0..2 {
        let response = router.route_chat(None, &request, Some("test")).await;
        assert!(response.status().is_success());
        axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
    }
    let calls = d.calls.lock().unwrap();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].0, calls[1].0);
}
