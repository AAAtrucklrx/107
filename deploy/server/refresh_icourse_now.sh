#!/usr/bin/env bash
# 手动触发评课库重爬（透传 refresh_course_db 参数，如 --dry-run / --check-only）
# 用法: ./refresh_icourse_now.sh [--dry-run|--check-only]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
LOG="$ROOT/deploy/server/logs/icourse_refresh.log"
log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
log "=== 手动触发: $* ==="
"$PY" "$ROOT/scripts/refresh_course_db.py" "$@" 2>&1 | tee -a "$LOG"
exit "${PIPESTATUS[0]}"
