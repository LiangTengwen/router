# PD Program Scheduling (M1 + M2)

Program-scoped scheduling for prefill/decode (PD) disaggregated serving. A *Program* is one logical agent task: a sequence of model requests separated by tool calls. Instead of routing each turn independently, the Router keeps a Program identity, prefers a stable decode (D) rank across turns, admits Program work against D capacity and fairness limits, bounds prefill (P) concurrency, and — when enabled — lets a returning turn read the KV that the previous turn left on its D source instead of recomputing it.

Design and milestones: [RFC #376](https://github.com/vllm-project/router/issues/376).
Implementation stages: M1 (cold remote) in [#388](https://github.com/vllm-project/router/pull/388); M2 (warm remote + automatic TTL) in [#461](https://github.com/vllm-project/router/pull/461).

## What each stage does

| Stage | Behavior | Default |
| --- | --- | --- |
| **M1** | Program identity, D-rank affinity, capacity-checked admission, bounded P prefill permits. Every turn is still a cold P→D handoff. | off (`pd.enabled=false`) |
| **M2** | A returning turn may **read** the retained KV from the same D source (`WarmRemote`) instead of letting P recompute the prefix; the Program may **keep its logical seat** across tool gaps through an automatically fitted TTL. Any unreadable/expired/mismatched source falls back to cold. | off (`pd.advanced.*` absent) |

M1 and M2 are independent switches. Without a Program identity, or with `pd.enabled=false`, requests follow the native Router policy and the native request-level PD path.

What this feature does **not** claim:

- no physical KV reservation on D — a logical seat is not a KV guarantee;
- no cross-turn compute reuse beyond the verified prompt prefix (no LocalAppend/Store in these stages);
- no guarantee that Program completion time improves on every workload; see [Validation](#validation) and [When to use it](#when-to-use-it).

## Prerequisites

1. **HTTP PD mode.** The Router is started with `--vllm-pd-disaggregation`, one `--prefill` target and at least one `--decode` target, and `--kv-connector nixl`.
2. **Matching vLLM build.** The advanced paths speak `vllm-0.28.0-pd-feedback-v1`: the engine must expose `GET /pd_capabilities` and return `kv_transfer_params` / `pd_feedback` for completed requests. `pd.advanced.protocol_revision` must match that revision.
3. **Per-DP-rank token capacity.** `pd.token_capacity_per_dp_rank` must equal the KV token capacity of one decode rank (for example `--kv-cache-memory-bytes / bytes_per_token`). It is required; Program scheduling is rejected without it.
4. **Program identity in requests** (see [Program identity](#program-identity)).
5. **M2 only:** decode engines must export reusable source blocks:
   `--kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both", ... "kv_connector_extra_config":{"backends":["UCX"],"bidirectional_kv_xfer":true,"decoder_kv_blocks_ttl":600}}'`.
   Without `bidirectional_kv_xfer` the Router keeps the cold path and reports `missing_candidate_metadata`.

## Quick start (M1)

Start the engines exactly as for native PD; the feature lives in the Router.

```bash
vllm-router \
  --host 0.0.0.0 --port 8000 \
  --vllm-pd-disaggregation \
  --prefill http://p0:8001 --decode http://d0:8002 --decode http://d1:8003 \
  --kv-connector nixl --policy round_robin \
  --enable-program-scheduling \
  --program-scheduling-config-json '{
    "pd": {"enabled": true, "max_prefill_requests_per_target": 4},
    "token_capacity_per_dp_rank": 873808,
    "enable_batch_gain_admission": false,
    "binding_strategy": "least_program_count",
    "global_queue": true,
    "max_active_programs_per_target": 8,
    "admission_waiting_request_threshold": 8,
    "max_acting_ttl_seconds": 60
  }'
```

`enable_batch_gain_admission`, `binding_only` and a missing `token_capacity_per_dp_rank` are rejected in PD mode by config validation; the uncalibrated batch-gain path is not part of PD Program scheduling.

## Quick start (M2 warm remote + automatic TTL)

```bash
vllm-router \
  --host 0.0.0.0 --port 8000 \
  --vllm-pd-disaggregation \
  --prefill http://p0:8001 --decode http://d0:8002 --decode http://d1:8003 \
  --kv-connector nixl --policy round_robin \
  --enable-program-scheduling \
  --program-scheduling-config-json '{
    "pd": {
      "enabled": true,
      "max_prefill_requests_per_target": 4,
      "advanced": {
        "warm_remote": true,
        "automatic_ttl": true,
        "ttl_online": true,
        "max_prefix_tokens": 131072,
        "capability_cache_seconds": 15
      }
    },
    "token_capacity_per_dp_rank": 873808,
    "enable_batch_gain_admission": false,
    "binding_strategy": "least_program_count",
    "global_queue": true,
    "max_active_programs_per_target": 8,
    "admission_waiting_request_threshold": 8,
    "max_acting_ttl_seconds": 60,
    "queue_timeout_seconds": 1800
  }'
```

Notes:

- `max_prefix_tokens` must cover the longest prompt you expect to reuse; exceeding it silently returns the request to M1 (counted as `request_shape_unsupported`).
- `queue_timeout_seconds` bounds how long a Program may wait in the Router queue. Retaining seats raises queue wait for other Programs; a `429 Program request exceeded its admission queue timeout` is the signal to raise it or lower `max_acting_ttl_seconds`.
- If you already have an offline calibration table (`pd.advanced.calibration`), `automatic_ttl` uses it and `ttl_online` is not needed.

## Program identity

The default `program_scheduling_enable_key` requires `vllm_xargs.agentic_context` in the request body:

```json
{
  "model": "pd-model",
  "prompt": [1, 2, 3, 4],
  "vllm_xargs": {
    "agentic_context": {
      "program_id": "session-42:lead",
      "task_id": "session-42",
      "session_id": "session-42",
      "agent_id": "lead",
      "agent_role": "lead",
      "parent_program_id": null,
      "priority": 0,
      "deadline_seconds": 30.0,
      "expected_output_tokens": 512,
      "kv_retention_ttl_seconds": 20.0
    }
  }
}
```

Rules enforced by the Router:

- `program_id` is the Program key. If omitted, it is derived from `task_id` + `agent_id` (default `lead`). Identical Program IDs under different served models stay separate.
- `parent_program_id` / `root_program_id` / `agent_role` / `blocks_parent` / `step_id` are optional lineage fields used for admission ordering.
- Hints (`priority`, `deadline(_seconds)`, `expected_output_length`/`expected_output_tokens`, `kv_retention_ttl(_seconds)`) live for exactly one request. The retention TTL is honored only with `pd.explicit_ttl=true`.
- Subagents should set `blocks_parent=true` when the parent must not proceed before the child finishes.
- `program_scheduling_enable_key: "auto"` additionally accepts `agent_hint` and supported framework session/agent headers; keep the canonical key in production so identity comes from one place.

For M2, `/v1/completions` requests must use token-id prompts and `return_token_ids: true` (plus `n=1`, `best_of=1`). Streaming requests must also set `stream_options.include_usage=true`: the completed turn is fenced against the reported completion-token count, so without the final usage chunk no candidate is published (counted as `missing_usage`). Chat-completion requests keep M1 behavior.

## How a turn is scheduled

1. Resolve the Program identity from the request; without one the request takes the native path.
2. Admit the turn: the Program's preferred D rank is checked against accounted capacity, the configured active-Program bound, and backend waiting-request thresholds. The turn waits in the Router queue if it does not fit.
3. Select P: the normal prefill policy picks a P target; a per-target permit bounds concurrent prefill bodies. The permit is released when the P body completes, not when D finishes.
4. Hand off P→D and finish the Program turn when the full D response is delivered. A successful completion settles usage and decides whether the Program keeps its seat (`Acting` TTL) or is released.
5. M2 only: on completion, the Router publishes a candidate describing the exact verified prefix and its source lease. A successor turn claims the candidate exclusively, re-checks the source epoch, and may read the KV instead of recomputing it.

Upstream failures are forwarded unchanged: if a prefill or decode target rejects a request (for example a 400 validation error, or a 429 with `Retry-After`), the client receives that status, body and headers instead of a generic Router 503. Only a failed **warm read** (5xx or transport failure on the reused source) triggers the single bounded cold retry; ordinary upstream errors are never retried. Local guard failures (for example a stream that ends before any output) remain Router 503s.

## Configuration reference

### Core Program scheduling

| Field | Default | Notes |
| --- | --- | --- |
| `token_capacity_per_dp_rank` | required in PD | KV tokens of one D rank; capacity and low-watermark checks use it |
| `max_active_programs_per_target` | `0` (auto) | auto = `2 × pd.max_prefill_requests_per_target`, clamped to `[4, 64]`; set explicitly to reproduce a run |
| `admission_waiting_request_threshold` | `0` (auto) | auto = `2 × pd.max_prefill_requests_per_target`, clamped to `[4, 64]`; `1` makes the active-Program bound ineffective |
| `binding_strategy` | `consistent_hash` | `consistent_hash`, `program_round_robin`, `least_program_count`, `reasoning_token_balance`, `cache_aware` |
| `global_queue` | `false` | one Router-wide Program queue instead of per-rank queues |
| `resume_order` | `mru` | `mru` or `fcfs` |
| `queue_timeout_seconds` | `600` | per-turn Router queue bound; exceeding it returns HTTP 429 |
| `force_resume_timeout_seconds` | `300` | relaxes growth reserve for a starved Program; capacity/count gates still apply |
| `paused_retention_ttl_seconds` | `1800` | how long a paused Program keeps its record |
| `decode_buffer_tokens` | `100` | per-request output reserve added to context accounting |
| `max_acting_ttl_seconds` | `10` | upper bound for the Acting TTL; raise it when tool gaps are long |
| `high_watermark_ratio` / `low_watermark_ratio` | `1.0` / `1.0` | admission watermarks on the accounted capacity |
| `max_segment_rounds` | `14` | maximum serving segment before yielding for fairness |
| `stats_window_size` | `100` | rolling observation window |
| `metrics_interval_seconds` | `1.0` | backend observation cadence (also stale-metric threshold) |
| `enable_batch_gain_admission` | `true` (must be `false` in PD) | uncalibrated batch-gain credit is rejected in PD mode |

### `pd` (PD-specific)

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `false` | master switch for PD Program scheduling |
| `max_prefill_requests_per_target` | `1` | P concurrency permits per prefill target |
| `explicit_ttl` | `false` | honor request-supplied `kv_retention_ttl` (bounded by `max_acting_ttl_seconds`) |
| `exclusive_capacity_accounting` | `true` | treat native D occupancy and Program reservations as the same KV instead of adding them |
| `prefill_kv_high_watermark` | `0.85` | when P KV usage is above it, tighten the Program-count cap back to the P permit count; `0` disables |
| `prefill_kv_window_seconds` | `10.0` | sampling window for the P watermark guard |
| `kv_events` | `false` | subscribe to KV events for advisory residency; never authorizes a transfer |
| `kv_hash_mode` | `sha256` | `sha256`, `python_hash_seed`, `xxhash_cbor` |

### `pd.advanced` (M2)

| Field | Default | Notes |
| --- | --- | --- |
| `protocol_revision` | `vllm-0.28.0-pd-feedback-v1` | must match the engine overlay |
| `warm_remote` | `false` | enable D→P warm reads |
| `automatic_ttl` | `false` | enable the automatic Acting TTL |
| `ttl_online` | `false` | fit the TTL from online rank statistics when no calibration table exists |
| `feedback` | `false` | use measured residency probability in TTL keep/drop |
| `max_prefix_tokens` | `32768` | largest verified prefix that may be published/pulled |
| `max_candidate_tokens` | `2097152` | total retained prefix tokens across candidates |
| `max_candidates` | `4096` | candidate-count bound |
| `max_metadata_bytes` | `262144` | bound on the `kv_transfer_params` metadata inspected per message |
| `max_response_bytes` | `8388608` | bound on a buffered non-streaming response body (text, token IDs, logprobs); metadata inspection keeps its own smaller bound, and the response tracker buffers up to the same bound so a large completion still publishes a candidate |
| `lease_margin_seconds` | `1.0` | safety margin before the source lease expires |
| `capability_cache_seconds` | `15.0` | Router-side `/pd_capabilities` cache; `0` disables; re-probed when the worker registry changes or an epoch mismatches |
| `warm_min_p_kv_usage` | `0.0` | only route warm when the target P's scraped KV usage is at least this value (`0` = always) |
| `warm_max_p_running` | unlimited | only route warm when the target P's scraped running requests are at most this value |
| `ttl_gamma` | `1.0` | wait-cost weight in the TTL solver |
| `ttl_min_samples` | `16` | interval samples required before the solver may return a positive TTL |
| `ttl_min_gain_seconds` | `0.001` | minimum utility for a positive TTL |
| `local_append`, `calibration` | off / none | LocalAppend/Store and calibrated costs are later stages; they require a versioned calibration table |

Configuration is strict: unknown fields inside `pd`/`pd.advanced` are rejected, and numeric bounds are validated at startup.

`warm_min_p_kv_usage` and `warm_max_p_running` read the per-P metrics scrape (`/metrics` of each prefill endpoint, sampled every `pd.prefill_kv_window_seconds`, values fresh for three windows). The scrape runs whenever warm reads are enabled, even if `pd.prefill_kv_high_watermark` is `0`. When a sample is missing or stale the gate stays open (warm is not suppressed) and `warm_skipped_pressure` only counts samples that actually blocked a warm read.

## Observability

`GET /get_loads` returns the live Program state under `program_scheduling`:

- `scheduler.ranks[]`: `target_id`, `dp_rank`, `configured_token_capacity`, `router_estimated_used_tokens`, `router_active_programs`, `router_queued_programs`, `vllm_running_requests`, `vllm_waiting_requests`, `observation_age_seconds`, `observation_fresh`, `capacity_accounting_source`, rolling averages (`avg_e2e_seconds`, `avg_router_queue_seconds`, `avg_context_growth_tokens`, …), and the per-rank Program list with `state`/`status`;
- `scheduler.prefill_cache_hit_rate` / `prefill_cache_healthy`: P-side prefix-cache signals used by the admission policy;
- `advanced`: `warm_enabled`, `automatic_ttl`, `candidate_count`, `counters`, `feedback`.

Counters worth watching first:

| Counter | Meaning |
| --- | --- |
| `candidate_published` / `candidate_claimed` | M2: a completed turn published a reusable candidate / a successor claimed it |
| `warm_external_read` / `warm_local_cache_hit` | warm route actually pulled KV from D / was satisfied by P cache |
| `candidate_rejected_*` | why a claim was refused (`prefix`, `identity`, `expiry`, `attempt`, `placement`) |
| `capability_cold_fallback`, `source_epoch_rejected` | capability probe failed or the source changed; the request stayed cold |
| `request_shape_unsupported` | request did not meet the exact-token M2 contract (text prompt, missing `return_token_ids`, `n>1`, over-budget context) |
| `missing_usage` | a completed turn had no usage report (streaming without `stream_options.include_usage`), so no candidate was published |
| `missing_candidate_metadata` | engine did not export source metadata (`bidirectional_kv_xfer` off?) |
| `automatic_ttl_positive` / `_zero` / `automatic_ttl_online` | TTL decisions |
| `expired_before_read`, `warm_read_cold_retry` | lease expired between claim and read / one bounded cold retry after a failed read |
| `capability_cache_hit` | Router reused a cached engine identity |

Router Prometheus metrics expose the native `vllm_router_processed_requests_total` split per worker, which is the quickest way to confirm M1 affinity: native round-robin stays near-even, while Program scheduling skews by Program count.

## When to use it

Enable M1 when agent traffic is multi-turn, several Programs share the deployment, and you want Program-level fairness and stable D placement instead of per-request round-robin.

Enable M2 when **P recomputation of shared prefixes is a meaningful part of turn latency**. Two conditions make the warm path valuable:

1. the deployment lets D retain and export blocks (`bidirectional_kv_xfer=true`, a `decoder_kv_blocks_ttl` longer than the typical tool gap), and
2. P's own prefix cache misses often (saturated P, working set larger than the cache, or restarted prefill workers).

If P already hits its own prefix cache, warm reads add little: the Router still selects the warm route, but the engine reports `warm_local_cache_hit` and no extra KV moves.

Do **not** enable automatic TTL when the interactive tail matters more than average Program completion: keeping seats across gaps can make later Programs wait. Start with `automatic_ttl=false`, then enable it with a bounded `max_acting_ttl_seconds` after checking `automatic_ttl_positive`, admission queue wait, and request p99.

## Tuning recipes

**Small deployment, few Programs (M1).** Keep `max_active_programs_per_target` at or above the expected concurrent Program count; otherwise admission just adds queueing. Raise `admission_waiting_request_threshold` only if backend D queues are long.

**Saturated P, long shared prefixes (M2).** Raise `max_prefix_tokens` above your longest prompt, keep `max_candidate_tokens` at roughly `max_prefix_tokens × expected concurrent Programs`, set `decoder_kv_blocks_ttl` above the p95 tool gap, and leave `warm_min_p_kv_usage` at `0` so warm reads are always considered. Expect the `warm_external_read` share to track P's miss rate.

**Tool gaps are long (tens of seconds to minutes).** M2 only helps if the source lease outlives the gap. Increase `decoder_kv_blocks_ttl`, then `max_acting_ttl_seconds`; automatic TTL stays zero until its interval window matures. A TTL beyond the source lease only skips re-admission, not recomputation.

**Admission is thrashing.** Check `router_queued_programs` and `queue_timeout_seconds`; a rising 429 rate means the active-Program bound or the seat TTL is too aggressive for the arrival pattern. Lower `max_acting_ttl_seconds` or raise `max_active_programs_per_target`/`queue_timeout_seconds`.

## Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Requests take the native path | no Program identity, `pd.enabled=false`, or `enable_key` set to `vllm_xargs.agentic_context` without `vllm_xargs.agentic_context` | check the request body and the Router startup config |
| Startup rejected | `token_capacity_per_dp_rank` missing, `binding_only`, `enable_batch_gain_admission`, or `pd.*` unknown fields | fix the config JSON (validation is strict) |
| `request_shape_unsupported` grows | text prompt, missing `return_token_ids`, `n>1`, or prompt longer than `max_prefix_tokens` | use token-id `/v1/completions` with `return_token_ids=true`, raise the bound |
| `missing_candidate_metadata` grows | decode engine does not export source blocks | set `bidirectional_kv_xfer=true` (and a `decoder_kv_blocks_ttl`) |
| `warm_local_cache_hit` dominates, no benefit | P cache already covers the prefix | expected; M2 has little to save in this regime |
| `capability_cold_fallback` / 503s | engine capability probe timing out under load | keep `capability_cache_seconds > 0` (default 15 s); the cache is fenced by registry revision and epoch |
| `candidate_rejected_prefix` grows | successor prompt does not start with the previous prompt | check that the framework sends the conversation prefix unchanged; rewrites/new branches are rejected by design |
| 429 admission timeouts | Programs wait longer than `queue_timeout_seconds` for a seat | raise the timeout, lower `max_acting_ttl_seconds`, or raise `max_active_programs_per_target` |
| Request p99 regresses | seat retention or affinity queueing | lower `max_acting_ttl_seconds`, disable `automatic_ttl`, or reduce affinity pressure |
| Client receives a backend 4xx/429 | upstream rejection forwarded unchanged (by design) | fix the request or back off using `Retry-After`; check `warm_read_cold_retry` to confirm read failures are still retried |
| `PD response envelope too large` | non-streaming completion exceeded `max_response_bytes` | raise it (bounded) or stream the response |

## Validation

Measured on the agentX trace replayed through AgentInfer with a 1P+2D Qwen3-Coder-30B-A3B-FP8 deployment (80 GiB KV per engine, `max-num-seqs=4`, ~55 K average context, 883 requests / 48.6 M input / 0.49 M output tokens per arm, identical workload fingerprints):

- **M1 alone** (19-session, moderate load): D prefix-cache hit and KV retransfer improved (0.81→0.93 hit, 956→354 MB transferred), but Program completion time did not improve and TTFT regressed ~13–15%, because transfer is a tiny fraction of turn latency there.
- **M2 warm + automatic TTL** (28-session, saturated P): Program JCT mean 5,898 s → 4,518 s (**−23.4%**), p50 −27.4%, TTFT mean −36.7%, makespan −23.5%, and P computed tokens 29.5 M → 6.4 M (**−78%**), with 883/883 requests. Guardrail: request p99 342 s → 646 s; automatic TTL did not add mean JCT over warm-only in this workload.

Full evidence and reproduction details are in PR [#461](https://github.com/vllm-project/router/pull/461). Treat these as one workload shape: long-context decode dominated; deployments where P recompute is not the bottleneck should expect smaller gains.

## Limitations and roadmap

- Logical admission and physical KV residency are separate. A seat is not a KV lease.
- Only the verified prompt prefix is reused; generated tokens are not assumed to be present in the successor prompt (trace replay rebuilds prompts from history).
- `LocalAppend`, `Store`, joint P×D routing, batch-gain credit and M4 event/opportunity feedback are later stages and remain off.
- Multi-Router deployments require Program ownership to be pinned to one Router; that work is not part of M1/M2.
- Program scheduling decisions assume fresh backend metrics; stale observations make the Router wait rather than guess.

See also: [Program scheduling internals](program_scheduling.md) for the native (non-PD) agent-aware scheduler, admission math, and calibration guidance.
