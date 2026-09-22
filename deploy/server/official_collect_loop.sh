#!/usr/bin/env bash
# 官方站点直采调度循环（方案 A；每日 05:45 运行；接 start_all/stop_all）
# 语义：每日一次 scripts/collect_official_pages.py（最近 90 天增量，每源 20 篇）
# 防护：state 文件防同日重复；失败不阻塞，下次再试；日志 logs/official_collect.log
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/deploy/server/run"
LOG="$ROOT/deploy/server/logs/official_collect.log"
PY="$ROOT/.venv/bin/python"
HOUR="${OFFICIAL_COLLECT_HOUR:-5}"
MINUTE="${OFFICIAL_COLLECT_MINUTE:-45}"
STATE="$RUN_DIR/official_collect_last_run.txt"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

log "官方直采调度循环启动（每日 ${HOUR}:${MINUTE}）"
while true; do
  now_h=$(date +%-H); now_m=$(date +%-M)
  today=$(date +%F)
  done_key=$(cat "$STATE" 2>/dev/null || echo "none")
  if [ "$done_key" != "$today" ] && \
     { [ "$now_h" -gt "$HOUR" ] || { [ "$now_h" -eq "$HOUR" ] && [ "$now_m" -ge "$MINUTE" ]; }; }; then
    log "=== 官方站点直采开始 ==="
    if "$PY" "$ROOT/scripts/collect_official_pages.py" --once --since-days 90 --limit 20 >> "$LOG" 2>&1; then
      log "=== 官方站点直采完成 ==="
    else
      log "[失败] 官方站点直采退出非零，明日再试"
    fi
    date '+%Y-%m-%d' > "$STATE"
  fi
  sleep 3600
done
