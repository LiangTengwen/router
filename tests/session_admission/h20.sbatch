#!/usr/bin/env bash
#SBATCH --job-name=ltw-session-admission
#SBATCH --partition=h20
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=03:00:00
set -euo pipefail
umask 077
run=$(pwd -P)
[[ "$run" == /home/david_cwq/ltw/session-admission-* ]]
: "${SLURM_JOB_ID:?}"
# These paths are previously verified read-only inputs on this cluster.
base=${SESSION_H20_VLLM_ENV:-/home/david_cwq/jinghaoyu/vllm028-env}
model=${SESSION_H20_MODEL:-/mnt/scratch/models/Qwen3-0.6B}
export PYO3_PYTHON="$base/bin/python" PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$run/tmp" XDG_CACHE_HOME="$run/cache" HF_HOME="$run/cache/hf" CUDA_CACHE_PATH="$run/cache/cuda"
export VLLM_CACHE_ROOT="$run/cache/vllm" VLLM_CONFIG_ROOT="$run/config/vllm" VLLM_ASSETS_CACHE="$run/cache/assets"
export TRITON_CACHE_DIR="$run/cache/triton" TORCHINDUCTOR_CACHE_DIR="$run/cache/torch" TORCH_HOME="$run/cache/torch-home"
export FLASHINFER_WORKSPACE_BASE="$run/cache/flashinfer" TORCH_EXTENSIONS_DIR="$run/cache/torch-extensions"
# Linux abstract sockets keep IPC off the shared filesystem.
export VLLM_RPC_BASE_PATH="@ltw-session-$SLURM_JOB_ID" XDG_RUNTIME_DIR="$run/runtime"
export NUMBA_CACHE_DIR="$run/cache/numba" HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1
export MPLCONFIGDIR="$run/cache/matplotlib" XDG_CONFIG_HOME="$run/config" XDG_DATA_HOME="$run/data"
export CARGO_HOME="$run/cargo" CARGO_TARGET_DIR="$run/target" CARGO_BUILD_JOBS=16 CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0
mkdir -p "$TMPDIR" "$XDG_RUNTIME_DIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$CARGO_HOME"
collect() {
    status=$?
    cd "$run"
    shopt -s nullglob
    files=(head-health.log compute-*.log input.sha256 router.sha256 "result-$SLURM_JOB_ID")
    existing=()
    for file in "${files[@]}"; do [[ ! -e "$file" ]] || existing+=("$file"); done
    tar czf "evidence-$SLURM_JOB_ID.tar.gz" "${existing[@]}" || true
    exit "$status"
}
trap collect EXIT
bash h20-health.sh compute "$run" >"compute-before-build-$SLURM_JOB_ID.log" 2>&1
sha256sum -c input.sha256
tar xzf input.tar.gz
test -r "$model/config.json"
compgen -G "$model/*.safetensors" >/dev/null
# Invoke the toolchain binaries directly. Rustup and the shared Cargo home are never written.
for tools in /home/david_cwq/.rustup/toolchains/{1.99.1-*,stable*} /home/david_cwq/jinghaoyu/.rustup/toolchains/{stable*,*}; do
    if [[ -x "$tools/bin/cargo" && -x "$tools/bin/rustc" ]]; then break; fi
done
test -x "$tools/bin/rustc"
export PATH="$tools/bin:$base/bin:/home/david_cwq/nyc/agentinfer-dp2/build-tools/bin:$PATH"
ssl=/home/david_cwq/ltw/pd-program-ba6a9ff-20261010/isolated-openssl
if [[ -d "$ssl/lib" ]]; then export OPENSSL_DIR="$ssl" LD_LIBRARY_PATH="$ssl/lib:${LD_LIBRARY_PATH:-}"; fi
export VLLM_SERVER_DEV_MODE=1 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false
cd "$run/src"
cargo --version
rustc --version
"$PYO3_PYTHON" -c 'import vllm, starlette, uvicorn; print(vllm.__version__)'
cargo --config "$run/vendor.toml" build --offline --locked --bin vllm-router
sha256sum "$CARGO_TARGET_DIR/debug/vllm-router" > "$run/router.sha256"
bash tests/session_admission/h20-health.sh compute "$run" >"$run/compute-before-engines-$SLURM_JOB_ID.log" 2>&1
# The harness creates its own process groups, monitors health, stops only those children,
# and writes fresh results separately for each run. Both GPUs stay in this allocation.
export SESSION_H20_RUN="$run"
"$PYO3_PYTHON" tests/session_admission/acceptance.py --router "$CARGO_TARGET_DIR/debug/vllm-router" \
    --vllm "$base/bin/vllm" --model "$model" --output "$run/result-$SLURM_JOB_ID"
