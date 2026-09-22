#!/usr/bin/env bash
# 评课库月度全量重爬调度循环（独立 setsid nohup 进程；随 start_all/stop_all 联动）
# 语义：每月 ICOURSE_DAY 日 ICOURSE_HOUR 时执行 refresh_course_db.py 全量 SOP
# 防护：月度 state（run/icourse_last_run.txt）防月内重跑；失败重试 1 次（1h 后）；磁盘 <3G 拦截。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/deploy/server/run"
LOG="$ROOT/deploy/server/logs/icourse_refresh.log"
PY="$ROOT/.venv/bin/python"
DAY="${ICOURSE_DAY:-1}"
HOUR="${ICOURSE_HOUR:-2}"
STATE="$RUN_DIR/icourse_last_run.txt"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

month_key() { date '+%Y-%m'; }

run_refresh() {
  local attempts=0
  while [ "$attempts" -lt 2 ]; do
    attempts=$((attempts + 1))
    log "=== 开始全量重爬（第 ${attempts} 次尝试）==="
    # 磁盘预检：库文件 ~58MB ×2 余量
    avail_kb=$(df -Pk "$ROOT/data" | awk 'NR==2 {print $4}')
    if [ "${avail_kb:-0}" -lt $((3 * 1024 * 1024)) ]; then
      log "[拦截] 磁盘余量 <3G（${avail_kb}KB），本月跳过"
      return 1
    fi
    if "$PY" "$ROOT/scripts/refresh_course_db.py" >> "$LOG" 2>&1; then
      log "=== 全量重爬完成 ==="
      date '+%Y-%m-%d %H:%M:%S' > "$STATE"
      return 0
    fi
    log "[失败] 第 ${attempts} 次尝试退出非零；重试间隔 1h"
    [ "$attempts" -lt 2 ] && sleep 3600
  done
  log "[告警] 本月重爬连续 2 次失败，保留旧库，下月再试"
  return 1
}

log "月度调度循环启动（每月 ${DAY} 日 ${HOUR} 时）"
while true; do
  now_day=$(date +%-d)
  now_hour=$(date +%-H)
  already=$(cat "$STATE" 2>/dev/null || echo "none")
  if [ "$now_day" = "$DAY" ] && [ "$now_hour" -ge "$HOUR" ] && [ "$already" != "$(month_key)" ]; then
    run_refresh || true
  fi
  # 每小时醒一次探测（避免长 sleep 漂移与夏令时问题）
  sleep 3600
done
