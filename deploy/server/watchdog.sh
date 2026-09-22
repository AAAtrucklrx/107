#!/usr/bin/env bash
# 小蜗看护：每 60s 探活三服务，死了自动拉起（nohup+setsid 隔离，配 start_all/stop_all 联动）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/deploy/server/run"
LOG="$ROOT/deploy/server/logs/watchdog.log"
PY="$ROOT/.venv/bin/python"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

is_up() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

start_one() { # $1=name $2=pidfile $3=logfile $4+=cmd
  local name="$1" pidfile="$2" logfile="$3"; shift 3
  nohup "$@" >>"$logfile" 2>&1 &
  echo $! > "$pidfile"
  log "[拉起] $name pid $!"
}

while true; do
  # 1) Web：进程 + 健康端点双检
  if is_up "$RUN_DIR/web.pid"; then
    if ! curl -s -m 6 -o /dev/null "http://127.0.0.1:8000/api/v1/health/live"; then
      log "[异常] web 进程在但 live 探测失败（可能正在初始化/健康探针慢）——观察下一轮"
    fi
  else
    log "[死亡] web —— 拉起"
    start_one xiaowo-web "$RUN_DIR/web.pid" "$ROOT/deploy/server/logs/web.log" \
      "$PY" -m uvicorn xiaowo_web.main:app --host 0.0.0.0 --port 8000
  fi
  # 2) Worker
  if ! is_up "$RUN_DIR/worker.pid"; then
    log "[死亡] worker —— 拉起"
    start_one xiaowo-worker "$RUN_DIR/worker.pid" "$ROOT/deploy/server/logs/worker.log" \
      "$PY" -m xiaowo_web.worker
  fi
  # 3) Streamlit 回退
  if ! is_up "$RUN_DIR/streamlit.pid"; then
    log "[死亡] streamlit —— 拉起"
    start_one xiaowo-streamlit "$RUN_DIR/streamlit.pid" "$ROOT/deploy/server/logs/streamlit.log" \
      "$PY" -m streamlit run app_test.py --server.port 8502 --server.headless true
  fi
  sleep 60
done
