#!/usr/bin/env bash
# 小蜗每日数据备份循环（独立 setsid nohup；随 start_all/stop_all 联动；2026-09-03）
# 备份范围（7 项）：review.db / xiaowo.db / course_data.db / web_evidence /
#                   chroma_db / programs(_jw) / .env（+ deploy 脚本）
# 保留：daily 7 份 + weekly（每周五）5 份；SQLite 用官方 .backup API（WAL 一致）。
# 用法：backup_daily.sh            # 常驻循环（每日 04:10）
#       backup_daily.sh --once     # 立即执行一次（幂等）
#       backup_daily.sh --dry-run  # 只打印清单
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/deploy/server/run"
LOG="$ROOT/deploy/server/logs/backup.log"
PY="$ROOT/.venv/bin/python"
BACKUP_ROOT="$ROOT/data/backups"
HOUR="${BACKUP_HOUR:-4}"
MINUTE="${BACKUP_MINUTE:-10}"
STATE="$RUN_DIR/backup_last_run.txt"
mkdir -p "$RUN_DIR"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; echo "[$(date '+%F %T')] $*"; }

run_backup() {
  local label="$1"  # daily/<date> 或 weekly/<date>
  local dir="$BACKUP_ROOT/$label"
  if [ -d "$dir" ] && [ -f "$dir/manifest.txt" ]; then
    log "跳过（已存在）: $dir"
    return 0
  fi
  local avail_kb
  avail_kb=$(df -Pk "$ROOT/data" | awk 'NR==2 {print $4}')
  if [ "${avail_kb:-0}" -lt $((5 * 1024 * 1024)) ]; then
    log "[拦截] 磁盘余量 <5G（${avail_kb}KB），备份中止"
    return 1
  fi
  mkdir -p "$dir"
  if [ "$(basename "$label" | head -c 1)" = "w" ]; then
    log "=== 周备份: $dir ==="
  else
    log "=== 日备份: $dir ==="
  fi
  # 1) SQLite 三库（backup API，在线一致性）
  "$PY" - "$ROOT" "$dir" <<'PYEOF' || { log "[失败] SQLite 备份"; return 1; }
import sqlite3, sys
root, out = sys.argv[1], sys.argv[2]
targets = {
    "review.db": f"{root}/data/review.db",
    "xiaowo.db": f"{root}/database/xiaowo.db",
    "course_data.db": f"{root}/data/course_data.db",
}
for name, src in targets.items():
    s = sqlite3.connect(src)
    d = sqlite3.connect(f"{out}/{name}")
    try:
        s.backup(d)
    finally:
        d.close(); s.close()
    print(f"  {name} -> {out}/{name}")
PYEOF
  # 2) 目录 tar（web_evidence / chroma / programs / deploy 脚本）
  tar -czf "$dir/artifacts.tar.gz" -C "$ROOT" \
    data/web_evidence knowledge/chroma_db scripts/data/programs_jw scripts/data/programs \
    deploy/server 2>/dev/null || { log "[失败] artifacts tar"; return 1; }
  # 3) .env（敏感，权限 600）
  cp "$ROOT/.env" "$dir/env.txt" 2>/dev/null && chmod 600 "$dir/env.txt"
  # 4) manifest
  {
    echo "backup_at: $(date '+%F %T')"
    echo "objects: review.db xiaowo.db course_data.db artifacts.tar.gz env.txt"
    echo "sizes:"
    du -h "$dir"/* | sed 's/^/  /'
  } > "$dir/manifest.txt"
  log "完成: $dir ($(du -sh "$dir" | cut -f1))"
  return 0
}

prune() {
  # daily 保留 7 份；weekly 保留 5 份
  local n=0
  ls -d "$BACKUP_ROOT"/daily/*/ 2>/dev/null | sort -r | tail -n +8 | while read -r d; do
    rm -rf "$d" && echo "[prune] $d" >> "$LOG"
  done
  ls -d "$BACKUP_ROOT"/weekly/*/ 2>/dev/null | sort -r | tail -n +6 | while read -r d; do
    rm -rf "$d" && echo "[prune] $d" >> "$LOG"
  done
}

if [ "${1:-}" = "--dry-run" ]; then
  echo "备份清单（dry-run）:"
  echo "  1) $(date +%F) 数据库 ×3（.backup API）：data/review.db database/xiaowo.db data/course_data.db"
  echo "  2) tar：data/web_evidence knowledge/chroma_db scripts/data/programs_jw scripts/data/programs deploy/server"
  echo "  3) .env（600） 4) manifest.txt"
  echo "  目录：$BACKUP_ROOT/{daily,weekly}/$(date +%F)/"
  exit 0
fi

if [ "${1:-}" = "--once" ]; then
  run_backup "daily/$(date +%F)" && prune
  exit $?
fi

log "备份循环启动（每日 ${HOUR}:${MINUTE}；每周五额外留 weekly 拷贝）"
while true; do
  now_h=$(date +%-H); now_m=$(date +%-M); dow=$(date +%u)
  done_key=$(cat "$STATE" 2>/dev/null || echo "none")
  today=$(date +%F)
  if [ "$done_key" != "$today" ] && \
     { [ "$now_h" -gt "$HOUR" ] || { [ "$now_h" -eq "$HOUR" ] && [ "$now_m" -ge "$MINUTE" ]; }; }; then
    run_backup "daily/$today" || true
    if [ "$dow" = "5" ]; then
      run_backup "weekly/$today" || true
    fi
    prune
    date '+%Y-%m-%d' > "$STATE"
  fi
  sleep 3600  # 每小时醒一次（避免长 sleep 漂移）
done
