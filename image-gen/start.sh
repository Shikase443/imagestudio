#!/bin/bash
set -euo pipefail

log() { printf '%s %s\n' "$(date -Is)" "$*"; }
started=$(date +%s)
log 'モデルファイル検証開始'
while read -r expected_size expected_hash relative_path; do
    actual_size=$(stat -c %s "/models/$relative_path")
    if [ "$actual_size" != "$expected_size" ]; then
        log "モデル容量不一致 file=$relative_path expected=$expected_size actual=$actual_size"
        exit 1
    fi
    printf '%s  %s\n' "$expected_hash" "/models/$relative_path" | sha256sum --check --status
    log "モデル検証成功 file=$relative_path bytes=$actual_size"
done < /config/model-checks.txt
log "モデルファイル検証終了 elapsed_seconds=$(($(date +%s) - started))"

# cgroupの実測メモリ量はGPU専用VRAM量やロード進捗率として表示しない。
memory_bytes() { cat /sys/fs/cgroup/memory.current; }
load_started=$(date +%s)
log "モデルロード開始 cgroup_memory_bytes=$(memory_bytes)"
/usr/local/bin/sd-server "$@" &
server_pid=$!
/bin/bash /config/telemetry.sh gen /usr/local/bin/sd-server "$server_pid" &
telemetry_pid=$!
trap 'kill -TERM "$server_pid" "$telemetry_pid" 2>/dev/null || true; wait "$server_pid" || true' TERM INT

ready=0
last_report=$load_started
while kill -0 "$server_pid" 2>/dev/null; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:8001/v1/models >/dev/null; then
        log "モデルロード終了 API待受確認成功 elapsed_seconds=$(($(date +%s) - load_started)) cgroup_memory_bytes=$(memory_bytes)"
        ready=1
        break
    fi
    now=$(date +%s)
    if [ "$((now - last_report))" -ge 30 ]; then
        log "モデルロード継続 elapsed_seconds=$((now - load_started)) cgroup_memory_bytes=$(memory_bytes)"
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
    log "モデルロード失敗 exit_code=$status elapsed_seconds=$(($(date +%s) - load_started)) cgroup_memory_bytes=$(memory_bytes)"
    [ "$status" != 0 ] || status=1
else
    log "画像生成サーバー終了 exit_code=$status"
fi
exit "$status"
