#!/bin/bash
set -euo pipefail
log() { printf '%s %s\n' "$(date -Is)" "$*"; }
started=$(date +%s)
log '解析モデル検証開始'
while read -r expected_size expected_hash relative_path; do
    actual_size=$(stat -c %s "/models/$relative_path")
    if [ "$actual_size" != "$expected_size" ]; then
        log "モデル容量不一致 file=$relative_path expected=$expected_size actual=$actual_size"
        exit 1
    fi
    printf '%s  %s\n' "$expected_hash" "/models/$relative_path" | sha256sum --check --status
    log "解析モデル検証成功 file=$relative_path bytes=$actual_size"
done < /config/model-checks.txt
log "解析モデル検証終了 elapsed_seconds=$(($(date +%s) - started))"

# cgroupの使用量とGPUメモリは別の実測指標として扱う。
memory_bytes() { cat /sys/fs/cgroup/memory.current; }
load_started=$(date +%s)
log "解析モデルロード開始 cgroup_memory_bytes=$(memory_bytes)"
/usr/local/bin/llama-server "$@" &
server_pid=$!
/bin/bash /config/telemetry.sh vlm /usr/local/bin/llama-server "$server_pid" &
telemetry_pid=$!
trap 'kill -TERM "$server_pid" "$telemetry_pid" 2>/dev/null || true; wait "$server_pid" || true' TERM INT
ready=0
last_report=$load_started
while kill -0 "$server_pid" 2>/dev/null; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:8002/health >/dev/null; then
        log "解析モデルロード終了 API待受確認成功 elapsed_seconds=$(($(date +%s) - load_started)) cgroup_memory_bytes=$(memory_bytes)"
        ready=1
        break
    fi
    now=$(date +%s)
    if [ "$((now - last_report))" -ge 30 ]; then
        log "解析モデルロード継続 elapsed_seconds=$((now - load_started)) cgroup_memory_bytes=$(memory_bytes)"
        last_report=$now
    fi
    sleep 1 & wait $! || true
done
set +e
wait "$server_pid"
status=$?
kill "$telemetry_pid" 2>/dev/null || true
set -e
if [ "$ready" = 0 ]; then
    log "解析モデルロード失敗 exit_code=$status elapsed_seconds=$(($(date +%s) - load_started))"
    [ "$status" != 0 ] || status=1
else
    log "解析サーバー終了 exit_code=$status"
fi
exit "$status"
