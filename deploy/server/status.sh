#!/usr/bin/env bash
# 查看小蜗服务状态
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/deploy/server/run"
for f in "$RUN_DIR"/*.pid; do
  [ -e "$f" ] || continue
  name="$(basename "$f" .pid)"
  pid="$(cat "$f" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "[RUNNING] $name pid $pid"
  else
    echo "[DEAD]    $name (pid $pid 不存在)"
  fi
done
echo "--- 端口 ---"
ss -tlnp 2>/dev/null | grep -E ':(8000|8502)\b' || echo "  8000/8502 无监听"
echo "--- 健康 ---"
curl -s -m 5 http://127.0.0.1:8000/api/v1/health/live; echo
