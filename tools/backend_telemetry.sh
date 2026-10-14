#!/bin/bash
set -euo pipefail
backend=$1
executable=$2
server_pid=$3
mkdir -p /output/telemetry
# 各バックエンドの名前空間で自身のGPU使用量を測定し、数値だけを共有する。
while kill -0 "$server_pid" 2>/dev/null; do
    gpu=$(nvidia-smi --query-compute-apps=process_name,used_gpu_memory --format=csv,noheader,nounits 2>/dev/null | awk -F, -v executable="$executable" '$1 == executable {gsub(/ /,"",$2); print $2}' || true)
    if [[ ! "$gpu" =~ ^[0-9]+$ ]]; then gpu=null; fi
    printf '{"gpu_mib":%s,"cgroup_memory_bytes":%s,"measured_at":%s}\n' \
        "$gpu" "$(cat /sys/fs/cgroup/memory.current)" "$(date +%s)" > "/output/telemetry/$backend.json.tmp"
    mv "/output/telemetry/$backend.json.tmp" "/output/telemetry/$backend.json"
    sleep 1
done
