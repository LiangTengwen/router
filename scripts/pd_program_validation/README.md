# Fixed-workload PD Program validation

This harness reproduces the 2026-10-11–12 synthetic four-turn comparison. It is a
deployment benchmark, not an agent quality evaluation or an assertion that every
RFC milestone is available. Run engines only inside a valid three-GPU SLURM
allocation. Capture a head snapshot before submitting each experiment; the runner
also captures allocation, node, process, port, IPC, and GPU snapshots before each
Router start. It only terminates process groups that it creates.

Prepare an isolated `PD_VALIDATION_ROOT` containing:

- `bin/main`: main commit `bc16b190f8875a275287dd70ce5b4c9e54373dd6`;
- `bin/candidate`: initial M1 commit `ba6a9ff`;
- `bin/permit-fix`: P-permit fix commit `3d10990`;
- `bin/token-aware`: exact token-array accounting commit `e11695a`;
- `head_snapshot.py` from this directory;
- `isolated-binary-provenance.json`: a JSON object mapping the four binary names
  above to their SHA-256 digests;
- `ISOLATED_BUILD_COMPLETE`: create only after confirming source revisions,
  distinct binary digests, and PD CLI enablement.

Use separate Cargo target directories and the same build profile/dependencies for
each revision. Reusing root-crate artifacts across worktrees produced a wrong
binary during preparation; those attempts were excluded. Never compare binaries
whose provenance has not been checked. The runner verifies all four digests.
The Linux run used Rust/Cargo 1.98.1, `cargo build --locked --bin vllm-router`,
`CARGO_PROFILE_DEV_DEBUG=0`, and the same main `Cargo.lock`. Both builds used a
build-only `rlib` crate type to avoid Python-extension linking; the branch's
production `Cargo.toml` was not changed. Match this profile for reproduction,
or rebuild every arm in release mode and treat that as a separate experiment.

The deployment used vLLM 0.28.0, Torch 2.13.0+cu130 and NIXL 1.3.2 with an isolated
NIXL package directory, and three H20 GPUs on the same node. Configure a compatible
Python environment without replacing shared packages. Inside the allocation:

```sh
export PD_VALIDATION_ROOT=/path/to/isolated/validation
export PD_VALIDATION_MODEL=/path/to/Qwen3_5-27B/model
export UCX_TLS=tcp,cuda_copy,cuda_ipc,sm,self
export UCX_NET_DEVICES=lo
export OMP_NUM_THREADS=8
export TOKENIZERS_PARALLELISM=false
export NIXL_LOG_LEVEL=WARN
export VLLM_SSM_CONV_STATE_LAYOUT=DS
export VLLM_SERVER_DEV_MODE=1
python -u /path/to/scripts/pd_program_validation/run.py
```

For batch submission, export these variables in the submitting shell, capture
and inspect the head snapshot, then use the scheduler (substitute the interpreter
and script paths):

```sh
python3 /path/to/scripts/pd_program_validation/head_snapshot.py > head-before.json
sbatch --partition=h20 --nodes=1 --ntasks=1 --gres=gpu:h20:3 \
  --cpus-per-task=36 --mem=320G --time=02:00:00 \
  --wrap='/path/to/validated/python -u /path/to/scripts/pd_program_validation/run.py'
```

Run the snapshot command on the head through system SSH; it performs no GPU
inspection. Do not submit while unresolved memory, storage, scheduler, port or
resource-ownership problems remain. The allocation's runner performs GPU checks.

`VLLM_SERVER_DEV_MODE` enables cache reset on the harness's private loopback
servers. Ports 38510–38512, 18480–18481 and 39510–39512 must be free. The runner
requires exactly three visible GPUs, verifies their UUIDs against the allocation,
and aborts if initial assigned memory is occupied. Engine settings and CPU
affinities are captured in command JSON files. Budget enough allocation time:
long-context Prefill can take several minutes per run. A new allocation must
repeat complete pairs; do not pair results across different allocations.

After a recorded diagnosis of a transient system monitor wait, an optional
`PD_VALIDATION_MONITOR_IDENTITY_FILE` can supply its exact PID, parent PID,
start-time fields, command and parent command. Only a matching identity receives
up to ten bounded state rechecks; it must return to S/R before proceeding. A
persistent D state or another blocked process still aborts startup, and all GPU,
memory, storage and port checks remain mandatory. The harness never terminates
system monitors. Do not configure this without first recording the diagnosis.

Each profile uses identical request payloads in all selected arms, resets engine
caches between arms, and checks completed SSE, exact input/output token counts,
and successful work. There are three repeats, with arm order reversed in repeat
1. `PD_VALIDATION_PROFILES=pressure` can select a complete profile, or specify a
comma-separated subset of `light,pressure,prefill-heavy`.

The follow-up short-output comparison selects
`PD_VALIDATION_PROFILES=prefill-heavy` and
`PD_VALIDATION_ARMS=main,cohort,paced`. `paced` uses the same token-aware binary
and configuration as `cohort`, changing only P concurrency from 8 to 4. All
selected arms still run three repeats in one allocation; the second repeat
reverses their order. The default remains the original five arms. Keep incomplete
earlier profile pairs as diagnostics; do not combine them with the new allocation.

The subsequent D-concurrency comparison selects
`PD_VALIDATION_PROFILES=pressure,prefill-heavy` and
`PD_VALIDATION_ARMS=main,bounded`. `bounded` keeps P concurrency 4 and lowers
the per-D active Program limit from 4 to 2; other settings match `paced`.
It tests whether reducing actual D concurrency can retain JCT savings while
meeting the stricter tail gate. Keep every rejected configuration in the report.

The accepted comparison (allocation 4927) uses `PD_VALIDATION_ARMS=main,serial` for both long-context
profiles. `serial` permits one active Program per D, uses P concurrency 4 and
metrics interval 0.1 seconds, and raises queue/force timeouts to 300 seconds so
the last client can wait for a Program seat without premature churn. Request
payloads and client timeout remain identical across arms. This configuration
may exchange first-response waiting or batch makespan for lower mean Program
JCT; report those costs along with the tail guard instead of assuming they improve.

```sh
python scripts/pd_program_validation/analyze.py "$PD_VALIDATION_ROOT/gpu-JOB_ID" --require-linear
python scripts/pd_program_validation/inspect.py "$PD_VALIDATION_ROOT/gpu-JOB_ID"
```

Acceptance requires all three paired JCT reductions to be at least 10%, all
paired TPOT p95 ratios to be at most 1.05, and identical successful token work.
Final verification requires both the original lower quantile and the linear
interpolated TPOT p95 to meet the tail gate. `--require-linear` validates raw
request counts/usage and rejects missing or incomplete raw data. Omitting it is
only useful for provisional inspection of summary files.
JCT runs from a client's first turn to the completion of its fourth turn,
including Router waiting and deterministic tool gaps. In the light profile the
client executor processes eight Programs with four concurrent clients; JCT starts
when a client task starts, not when it enters the executor queue. Other profiles
start all sixteen clients concurrently. The reported p95 uses the existing
harness's lower empirical order statistic. The original experiment retained
per-request timing and JCT aggregates; this reproduction script additionally saves
each Program JCT sample for other quantile methods.

`inspect.py` measures native prefix-cache counter deltas and preemptions separately
for P and both D engines, and compares generated text. D cache hits include
connector-supplied context and must not be interpreted as P compute savings.
Token counts alone do not prove quality equivalence. Fixed seed and temperature
zero also do not establish identical online serving output under differing batch
schedules; retain and report text differences, and run application quality
evaluation before production rollout.

The completed paired results and rejected configurations are documented in
[the validation report](../../docs/pd_program_scheduling_validation.md), with
[a machine-readable manifest](../../docs/pd_program_scheduling_validation_results.json).
The final `serial` example is conditional tuning for the mean-JCT objective;
it is not a production default and was not measured on the light profile.
