#!/usr/bin/env bash
# Run on head for coordination, or in a Slurm allocation for compute checks.
set -euo pipefail
role=${1:?head or compute}
run=${2:?private experiment directory}
test "$(realpath "$run")" = "$run"
[[ "$run" == /home/david_cwq/ltw/session-admission-* ]]
date --iso-8601=seconds
hostname
ping=$(scontrol ping)
echo "$ping"
[[ "$ping" == *UP* ]]
sinfo -h -p h20 -o '%P %a %D %t'
squeue -p h20 -o '%.12i %.12u %.10T %.18R %.10M %.15b'
free -m
uptime
ps -eo pid,ppid,stat,etimes,wchan:22,comm | awk 'NR==1 || $3 ~ /^[DZ]/'
timeout 8 stat "$run"
timeout 8 df -h "$run" /dev/shm
# Refuse to start at severe memory/storage pressure. Preserve the snapshot on failure.
awk '/MemAvailable:/ {exit ($2 < 1048576)}' /proc/meminfo
test "$(timeout 8 df -Pk "$run" | awk 'END {print $4}')" -gt 10485760
if [[ "$role" == compute ]]; then
    : "${SLURM_JOB_ID:?valid allocation required}"
    scontrol show job -d "$SLURM_JOB_ID"
    ps -eo pid,ppid,pgid,stat,etimes,comm --forest
    ss -ltnp
    ipcs -m
    timeout 8 ls -lan /dev/shm
    # CUDA logical indexes can differ from physical GPU indexes under Slurm.
    "${PYO3_PYTHON:?}" - <<'PY'
import os, subprocess, torch
assert torch.cuda.device_count() >= 2, "allocation needs two visible GPUs"
for i in range(2):
    props=torch.cuda.get_device_properties(i)
    assert "H20" in props.name and props.uuid, props
    uuid=str(props.uuid)
    if not uuid.startswith(("GPU-","MIG-")): uuid="GPU-"+uuid
    print(i,props.name,uuid,flush=True)
    result=subprocess.check_output(["nvidia-smi","-i",uuid,"--query-compute-apps=pid,process_name,used_memory","--format=csv,noheader"],text=True)
    print(result,flush=True)
    foreign=[row for row in result.splitlines() if row.split(",",1)[0].strip()!=str(os.getpid())]
    assert not foreign, "allocated GPU already has compute processes"
PY
else
    [[ "$role" == head ]]
fi
