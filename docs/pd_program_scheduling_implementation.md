# PD Program scheduling: first rollout

This branch implements **M1 (one-way, cold remote PD)** of [RFC #1](https://github.com/LiangTengwen/router/issues/1). It is an initial rollout, not an implementation or validation of every later RFC milestone. `VllmPDRouter` supports Program admission for HTTP chat and completions in both static-worker and vLLM discovery deployments. No Program identity, or a disabled scheduler, continues through the existing native PD path.

## Enablement

Use the existing `--enable-program-scheduling` and `--program-scheduling-config-json` options. The latter accepts this example:

```json
{
  "token_capacity_per_dp_rank": 100000,
  "enable_batch_gain_admission": false,
  "queue_timeout_seconds": 60,
  "force_resume_timeout_seconds": 30,
  "metrics_interval_seconds": 1,
  "pd": {
    "enabled": true,
    "explicit_ttl": false,
    "max_prefill_requests_per_target": 1,
    "kv_events": false,
    "kv_hash_mode": "sha256"
  }
}
```

The capacity is illustrative: set it from the actual D rank capacity and deploy appropriate low/high watermarks. Output limits, workload estimates, and engine metrics must use compatible token units. The launcher's routing mode must be `--vllm-pd-disaggregation`. Existing static `--prefill`/`--decode` or discovery options and connector selection continue to apply. Program metadata uses the existing `vllm_xargs.agentic_context` contract:

```json
{
  "model": "served-model",
  "prompt": "Complete input for this round",
  "max_tokens": 512,
  "vllm_xargs": {
    "agentic_context": {
      "program_id": "agent-task",
      "expected_resume": true
    }
  }
}
```

Configuration requires explicit per-rank token capacity, HTTP vLLM PD mode, `pd.enabled=true`, positive bounded P concurrency, `binding_only=false`, and `enable_batch_gain_admission=false`. Unknown PD fields, including unverified hot/local capability switches, are rejected. Regular scheduling keeps its existing defaults and behavior.

## Behavior and accounting

- D placement is Program scoped; P is selected by the configured prefill policy on every turn. Internal DP D rank headers are independent of the selected P rank. Static worker discovery learns rank targets; ZMQ discovery uses rank-qualified registrations or the registered/configured DP size.
- Same-generation requests are retained and dispatched serially. A successor waits for the **entire D body**, including the final stream boundary. P completion and D response headers do not put the Program into Acting.
- Admission retains the existing U/q/R ordinary capacity and fairness machinery. PD force deadlines can relax growth reserve but cannot bypass immediate capacity, Program count, or native waiting gates. Uncalibrated batch gain is disabled. Returning Active turns recheck input growth without adding their existing q twice.
- Native occupancy is an accounting floor. Pausing logical reservations does not imply physical KV release. PD metrics must be fresh and include KV usage and waiting counts; otherwise the request remains queued and can time out. Metrics collection captures the existing scheduler revision/checkpoint before I/O, preserving new reservations committed during collection.
- The wire output limit is reserved even when a client supplies a smaller output hint. With no wire limit, the configured decode buffer is used. A nonempty flat array of unsigned 32-bit token IDs on `/v1/completions` is counted directly; strings, chat messages, and other shapes retain estimates calibrated from successful usage. Token-array requests do not calibrate the text estimator from JSON metadata bytes. This is **logical admission**, not a proof that an engine can allocate the exact physical KV footprint. Engine capacity enforcement remains authoritative. Supply output limits and calibrate estimation for the deployed model/template, especially for multimodal input.
- P semaphore permits bound Program requests during Prefill. They are acquired after D reservation and released after the entire P response body is read, including in concurrent NIXL push/discovery paths. Waiting for D headers or its body cannot occupy a P slot. Errors and cancellation release the permit through its owning future. P remains a per-turn resource; these permits are not long-term P KV seats. This bound does not account for traffic sent directly to the engine outside this Router.
- Each dispatch has a unique attempt ID in addition to generation and placement epoch. Duplicate completion cannot settle a successor in the same placement. Admission cancellation and response Drop settle Router accounting exactly once; dropping an HTTP request/response closes its transport, but does not promise that an engine with no cancel protocol has already freed all physical work.
- SSE bytes and headers pass through unchanged. A successful stream needs a normal EOF and `[DONE]`; transport errors, explicit SSE errors, body Drop and incomplete EOF are failures. Usage inspection is bounded. Large JSON responses still pass through; missing usage falls back to estimates.
- Acting TTL defaults to zero. `pd.explicit_ttl=true` accepts the existing explicit request TTL, clipped to `max_acting_ttl_seconds`. TTL is logical protection only. Automatic TTL based on unvalidated monolithic recovery costs is not used.
- Authorization credentials and model are hashed into the internal scheduling pool to isolate equal external Program IDs. This is state isolation, not authentication. With gateway authentication that strips credentials, use authoritative tenant shards or a trusted tenant-aware identity adapter. Multi-Router coordination is not implemented; pin each Program to one authoritative Router.

The existing worker-load diagnostic endpoint adds `program_scheduling` only when PD Program scheduling is enabled. It includes rank accounting, placements, queues, and `warm_enabled=false`.

## Reusing upstream KV event management

[#320](https://github.com/vllm-project/router/issues/320) and [PR #326](https://github.com/vllm-project/router/pull/326) are reusable **residency infrastructure**. This branch cherry-picks source commit `eb1c78339a057bd9523584323087198f211e995c`, preserving its author, then integrates `KvIndexSupervisor` with P/D registration and removal. The fork baseline does not contain the earlier upstream `kv_hash` module. The event index does not depend on that module, so its unrelated declaration is omitted when resolving the import conflict.

Reusable pieces are the multi-tier compressed radix index, `(owner, tier)` claims, per-rank SUB ingestion, DEALER replay, MessagePack decoding, wildcard endpoint resolution, and shared-pool ownership. Set `pd.kv_events=true` to attach subscribers to registered P/D worker base URLs via `/get_server_info` and `/kv_event_sources`. Unsupported/disabled event endpoints are soft skips. Event discovery has a separate task and bounded HTTP timeouts, so it cannot stall capacity observations. Subscriber and observation tasks are stopped when the Router is dropped.

The integration additionally:

- hashes only complete token blocks and rejects invalid sequence-frame lengths;
- stops replay at holes or undecodable payloads rather than advancing through them;
- clears advisory worker residency on unrecovered live sequence gaps or malformed batches;
- rejects a publisher batch for the wrong DP rank;
- fences subscriber mutations during shutdown before removal clears worker residency;
- drops the supervisor with explicit subscriber shutdown.

Ingestion signals are advisory here. `Advance` or `ReplayApplied` alone never authorizes a warm route. The index does not grant a read lease, validate an exact request token/template prefix, prove connector layout compatibility, or provide transfer metadata with a verified expiry. Its upstream local hash currently omits cache salt/multimodal extra-key isolation, its topology is not garbage-collected, and the signals do not provide a complete per-rank trust state machine. Therefore **there is no KV-hit capacity credit or warm transfer consumer in M1**. Keep event subscription off by default; topology memory bounds and trust/identity extensions remain prerequisites for a long-running exact KV-aware rollout.

## Deferred milestones and deployment evidence

H20 deployment probes on 2026-10-11 completed a real NIXL P→D transfer and the requested 128 output tokens using vLLM 0.28.0 and NIXL 1.3.2. This establishes the tested one-way path only; it does not complete the RFC's broader M0 capability matrix. Fixed-workload GPU comparisons measure Program completion time, successful work, and TPOT tails. M2–M4 remain gated: NIXL D→P multi-turn candidate leasing and cold retry, keep/drop automatic TTL, calibrated joint costs, LocalAppend/TPOT constraints, shared Store access, warm migration and exact event feedback. The implementation does not infer these capabilities from `kv_both`, an event hit, or connector names.

See the [validation report](pd_program_scheduling_validation.md) for the workload, pinned versions, paired results and limitations. The [long-context configuration fragment](../examples/configs/pd_program_long_context.json) reproduces the `serial` arm for the tested one-P/two-D deployment. Pass it as `--program-scheduling-config-json`; it is not a complete Router configuration. Its rank capacity must be replaced with the deployed engine's measured value. Program requests also need the explicit short TTL from the report. This tuning limits histories competing for the P cache and active D Programs, prioritizing mean Program JCT with a decode-tail constraint. First-response waiting and batch makespan can increase; it is not a universal default or a physical KV lease.

Before opening any such path, fix a deployment version and verify actual rank routing, token prefixes, metadata ownership, leases, failures, and physical resource behavior as required by the RFC. H20 experiments must use system SSH and SLURM, with the workspace health snapshot before each start/restart.

## Validation

The regression suite includes all library tests, plus:

```sh
cargo test --lib --bin vllm-router \
  --test test_pd_program_scheduling \
  --test test_dp_routing \
  --test test_pd_routing
cargo fmt --all -- --check
```

Tests cover ordinary/forced capacity, physical occupancy, missing observations, returning input growth, output reservation, serial turns and waiter cancellation, duplicate/stale completion, full JSON/SSE settlement and incomplete streams, actual D rank affinity across HTTP chat/completion turns, per-turn P selection, response Drop, and opt-out/disabled native routing. Existing KV index and live ZMQ store/remove/clear tests exercise the reused foundation.

Validation on 2026-10-10: 706 library tests, 4 launcher tests, 18 existing DP integration tests, 5 new PD Program integration tests, and 2 existing PD integration tests passed (735 total). Formatting and `git diff --check` passed. Existing atomic-API deprecation warnings and the macOS linker unwind-table warning remain.

On 2026-10-11 the P permit boundary was corrected after a fixed-workload HTTP mock comparison identified accidental P/D serialization. Pretokenized completions now reserve their exact input token count. All 739 tests passed: 707 library, 4 launcher, 18 DP integration, 8 PD Program integration, and 2 PD integration tests. Coverage includes stalled P bodies followed by stalled D headers in both sequential and NIXL push paths, and token arrays whose JSON size would have caused excessive admission reservations. Formatting and `git diff --check` passed. HTTP mocks do not establish GPU throughput or KV reuse gains.

## Current four-task-duration target (2026-10-12)

The updated target requires mean, p50, p95 and p99 Program duration to be strictly
lower than main; TPOT and other performance metrics are observations, not gates.
Allocation 4949 passed all four metrics in each of three paired repeats for both
long-context profiles, with identical successful work. The source remains
e11695a; the configuration changes to four active Programs per D and P Prefill
concurrency eight. See the [current validation](pd_program_task_duration_validation.md)
and [tested configuration fragment](../examples/configs/pd_program_task_duration.json).
The earlier serial example and mean/TPOT acceptance remain historical results.
These synthetic tests do not establish the light-profile result, continuous-load
stability, business quality equivalence or horizontal scalability.
