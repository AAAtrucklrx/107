#!/usr/bin/env bash
# 停止全部小蜗服务（幂等，不存在的进程静默跳过）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$ROOT/deploy/server/run"
# 先停评课月度调度循环
if [ -f "$RUN_DIR/icourse_loop.pid" ]; then
  ip=$(cat "$RUN_DIR/icourse_loop.pid")
  kill "$ip" 2>/dev/null && echo "[icourse] 月度调度循环已停止 (pid $ip)"
  rm -f "$RUN_DIR/icourse_loop.pid"
fi

# 先停看护（避免它把刚停的服务拉起来）
if [ -f "$RUN_DIR/watchdog.pid" ]; then
  wp=$(cat "$RUN_DIR/watchdog.pid")
  kill "$wp" 2>/dev/null && echo "[guard] watchdog 已停止 (pid $wp)"
  rm -f "$RUN_DIR/watchdog.pid"
fi

for f in "$RUN_DIR"/*.pid; do
  [ -e "$f" ] || continue
  name="$(basename "$f" .pid)"
  pid="$(cat "$f" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    echo "[stop] $name (pid $pid)"
  else
    echo "[skip] $name 未在运行"
  fi
  rm -f "$f"
done
echo "--- 残留检查 ---"
for pat in "xiaowo_web.main" "xiaowo_web.worker" "streamlit run app_test"; do
  pgrep -af "$pat" | grep -v pgrep || echo "  无残留: $pat"
done
