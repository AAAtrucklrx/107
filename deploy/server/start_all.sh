#!/usr/bin/env bash
# 小蜗 Linux 服务器管理脚本 —— 沙箱内 nohup 托管（无 systemd）
# 用法: ./start_all.sh   ./stop_all.sh   ./status.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/deploy/server/run"
LOG_DIR="$ROOT/deploy/server/logs"
PY="$ROOT/.venv/bin/python"
mkdir -p "$RUN_DIR" "$LOG_DIR"

is_running() { # $1=pidfile
  [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

start_one() { # $1=name  $2=pidfile  $3=logfile  $4+=cmd...
  local name="$1" pidfile="$2" logfile="$3"; shift 3
  if is_running "$pidfile"; then
    echo "[skip] $name 已在运行 (pid $(cat "$pidfile"))"
    return 0
  fi
  nohup "$@" >>"$logfile" 2>&1 &
  echo $! > "$pidfile"
  echo "[start] $name pid $(cat "$pidfile") -> $logfile"
}

start_one xiaowo-web  "$RUN_DIR/web.pid"  "$LOG_DIR/web.log" \
  "$PY" -m uvicorn xiaowo_web.main:app --host 0.0.0.0 --port 8000

start_one xiaowo-worker "$RUN_DIR/worker.pid" "$LOG_DIR/worker.log" \
  "$PY" -m xiaowo_web.worker

start_one xiaowo-streamlit "$RUN_DIR/streamlit.pid" "$LOG_DIR/streamlit.log" \
  "$PY" -m streamlit run app_test.py --server.port 8502 --server.headless true

# LLM 平台暖启动守护已取消（2026-09-07：LLM 切 DeepSeek 官网 SaaS，无冷启动概念；
# 45s 心跳纯属浪费且计费。脚本 llm_warm.py 保留，切回科大平台时再启用）

# 每日备份调度循环（每日 04:10；7 项全量；保留日 7 份+周 5 份）
start_one xiaowo-backup "$RUN_DIR/backup.pid" "$LOG_DIR/backup.log" \
  bash "$ROOT/deploy/server/backup_daily.sh"

# 官方站点直采调度循环（每日 05:45：teach 等官方源 → 审核库）
start_one xiaowo-official-collect "$RUN_DIR/official_collect.pid" "$LOG_DIR/official_collect.log" \
  bash "$ROOT/deploy/server/official_collect_loop.sh"

# 业务哨兵（每 5min：搜索可用性 + 发布索引；状态供 readiness 读取）
start_one xiaowo-sentinel "$RUN_DIR/sentinel.pid" "$LOG_DIR/sentinel.log" \
  "$PY" "$ROOT/deploy/server/sentinel.py"

# 评课月度调度循环（若未运行则拉起）
if [ -f "$RUN_DIR/icourse_loop.pid" ] && kill -0 "$(cat "$RUN_DIR/icourse_loop.pid")" 2>/dev/null; then
  echo "[icourse] 月度调度循环已在运行"
else
  setsid nohup "$ROOT/deploy/server/icourse_refresh_loop.sh" >>"$LOG_DIR/icourse_refresh.log" 2>&1 &
  echo $! > "$RUN_DIR/icourse_loop.pid"
  echo "[icourse] 月度调度循环已启动 pid $(cat "$RUN_DIR/icourse_loop.pid")"
fi

# 看护（若未运行则拉起；stop_all 会一并停止）
if [ -f "$RUN_DIR/watchdog.pid" ] && kill -0 "$(cat "$RUN_DIR/watchdog.pid")" 2>/dev/null; then
  echo "[guard] watchdog 已在运行"
else
  setsid nohup "$ROOT/deploy/server/watchdog.sh" >>"$LOG_DIR/watchdog.log" 2>&1 &
  echo $! > "$RUN_DIR/watchdog.pid"
  echo "[guard] watchdog 已启动 pid $(cat "$RUN_DIR/watchdog.pid")"
fi

sleep 3
echo "--- 健康检查 ---"
curl -s -m 5 http://127.0.0.1:8000/api/v1/health/live; echo
curl -s -m 5 http://127.0.0.1:8000/api/v1/health/ready; echo
curl -s -m 5 -o /dev/null -w "SPA / -> %{http_code}\n" http://127.0.0.1:8000/
