#!/usr/bin/env bash
# 小蜗统一运维入口（2026-09-29）
#
#   bash deploy/server/ops.sh                 终端彩色总览（对接/巡检时直接看）
#   bash deploy/server/ops.sh html [--open]   生成单文件运维仪表盘（可打印/投屏）
#   bash deploy/server/ops.sh watch [秒]      每 N 秒刷新仪表盘（默认 30s，前台运行）
#   bash deploy/server/ops.sh onepager        生成《运维一页纸》（HTML + Markdown）
#   bash deploy/server/ops.sh logs <服务> [n] 看某服务日志（默认 60 行）
#   bash deploy/server/ops.sh restart <服务|all>
#   bash deploy/server/ops.sh net [--no-reactivate]   立刻体检出网（必要时重新开通网络通）
#   bash deploy/server/ops.sh web            运维台 Web 地址/账号/状态（端口转发看这个）
#   bash deploy/server/ops.sh status          等价于 deploy/server/status.sh
#
# 只读优先：默认所有子命令都不改服务状态；只有 restart / net（开通动作）会动生产。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
RUN_DIR="$ROOT/deploy/server/run"
LOG_DIR="$ROOT/deploy/server/logs"
DASH="$ROOT/deploy/server/ops_dashboard.html"
export OPS_ROOT="$ROOT"

cmd="${1:-overview}"; shift || true

case "$cmd" in
  overview|"")
    "$PY" - "$ROOT" <<'PYEOF'
import json, subprocess, sys
root = sys.argv[1]
snap = json.loads(subprocess.run([f"{root}/.venv/bin/python", f"{root}/deploy/server/ops_collect.py"],
                                 capture_output=True, text=True, timeout=180).stdout or "{}")
G, R, Y, N, B = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m"
def mark(ok, warn=False):
    return f"{G}●{N}" if ok else (f"{Y}●{N}" if warn else f"{R}●{N}")
svc = snap.get("services") or []
alive = sum(1 for s in svc if s.get("alive"))
health = snap.get("health") or {}
checks = health.get("checks") or []
ext = [e for e in (snap.get("egress") or []) if not e.get("internal")]
net_ok = all(e.get("ok") for e in ext)
sent = snap.get("sentinel") or {}
bk = ((snap.get("backup") or {}).get("daily") or {})
res = snap.get("resources") or {}
git = snap.get("git") or {}
tf = snap.get("traffic") or {}
print(f"\n{B}小蜗运维总览{N}  {snap.get('generated_at','')}")
print("─" * 74)
print(f" 服务      {mark(alive == len(svc))} {alive}/{len(svc)} 运行")
print(f" 健康门槛  {mark(health.get('status') == 'ready')} {health.get('status','-')}"
      f"   " + "  ".join(f"{mark(c.get('ok'))}{c.get('label','')}" for c in checks))
print(f" 校外出网  {mark(net_ok)} " + ("全部可达" if net_ok else
      "不通：" + "、".join(e.get("label", "?") for e in ext if not e.get("ok"))))
print(f" 搜索体检  {mark(bool(sent.get('search_ok')))} hits={sent.get('search_hits')} "
      f"连续失败={sent.get('fail_streak')} 检查于 {sent.get('checked_time','-')}")
print(f" 备份      {mark((bk.get('age_hours') or 999) < 30)} 最新 {bk.get('newest')} "
      f"（{bk.get('age_hours')} 小时前，{bk.get('count')} 份 / {bk.get('size_mb')} MB）")
print(f" 资源      负载 {res.get('load')} / {res.get('cpu_count')} 核 · "
      f"内存可用 {res.get('mem_avail_mb')} MB / {res.get('mem_total_mb')} MB · "
      f"磁盘 {res.get('disk_used_pct')}%")
print(f" 代码      {git.get('head','-')} · 未提交 {git.get('dirty')} 项")
print(f" 使用量    消息 {tf.get('messages')} 条 / 会话 {tf.get('conversations')} 个 · 最近 {tf.get('latest')}")
print("─" * 74)
for s in svc:
    flag = mark(s.get("alive")) if s.get("alive") else f"{R}○{N}"
    port = f":{s['port']}" if s.get("port") else ""
    print(f"  {flag} {s.get('name',''):<16} pid={str(s.get('pid') or '-'):<9} up={s.get('uptime','-'):<14}{port}")
print(f"\n 仪表盘: bash deploy/server/ops.sh html --open    一页纸: ops.sh onepager\n")
PYEOF
    ;;

  html)
    "$PY" "$ROOT/deploy/server/ops_dashboard.py" --out "$DASH" >/dev/null
    echo "[html] 已生成 $DASH"
    if [ "${1:-}" = "--open" ]; then
      # 服务器有图形会话（xrdp :10）；无桌面时自动跳过
      for d in :10 :0 :11; do
        if DISPLAY=$d timeout 6 xdpyinfo >/dev/null 2>&1; then
          DISPLAY=$d nohup firefox "$DASH" >/dev/null 2>&1 &
          echo "[html] 已在 DISPLAY=$d 打开浏览器"
          break
        fi
      done
    fi
    ;;

  watch)
    interval="${1:-30}"
    echo "[watch] 每 ${interval}s 刷新 $DASH（Ctrl+C 结束）"
    while true; do
      "$PY" "$ROOT/deploy/server/ops_dashboard.py" --out "$DASH" --refresh "$interval" >/dev/null
      echo "[watch] $(date '+%H:%M:%S') 已刷新"
      sleep "$interval"
    done
    ;;

  onepager)
    "$PY" "$ROOT/deploy/server/ops_onepager.py"
    ;;

  logs)
    svc="${1:?用法: ops.sh logs <服务名> [行数]}"; n="${2:-60}"
    f="$LOG_DIR/$(basename "${svc%.log}").log"
    [ -f "$f" ] || { echo "找不到日志：$f"; echo "可用：$(ls "$LOG_DIR" | tr '\n' ' ')"; exit 1; }
    tail -n "$n" "$f"
    ;;

  restart)
    svc="${1:?用法: ops.sh restart <服务名|all>}"
    if [ "$svc" = "all" ]; then
      echo "[restart] 全量重启"; bash "$ROOT/deploy/server/stop_all.sh"; sleep 2; bash "$ROOT/deploy/server/start_all.sh"
    else
      pf="$RUN_DIR/$(basename "${svc%.pid}").pid"
      [ -f "$pf" ] || { echo "找不到 pidfile：$pf"; exit 1; }
      pid=$(cat "$pf"); echo "[restart] 停止 $svc (pid $pid)"; kill "$pid" 2>/dev/null; sleep 2
      bash "$ROOT/deploy/server/start_all.sh" | grep -E "start|skip" | head -12
    fi
    ;;

  web)
    port="$("$PY" -c "import os;print(os.environ.get('XIAOWO_OPS_PORT','8899'))")"
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo "运维台 Web："
    echo "  本机访问     http://127.0.0.1:${port}/"
    echo "  内网/转发目标 http://${ip}:${port}/   （公网转发到该端口即可）"
    echo "  路由         / 仪表盘 · /onepager 一页纸 · /json 原始快照 · /health 存活"
    if [ -f "$RUN_DIR/ops_web.pid" ] && kill -0 "$(cat "$RUN_DIR/ops_web.pid")" 2>/dev/null; then
      echo "  状态         运行中 (pid $(cat "$RUN_DIR/ops_web.pid"), up $(ps -o etime= -p "$(cat "$RUN_DIR/ops_web.pid")" | tr -d ' '))"
    else
      echo "  状态         未运行（bash deploy/server/start_all.sh 拉起）"
    fi
    "$PY" "$ROOT/deploy/server/ops_web.py" --print-credentials | sed 's/^/  /'
    echo "  重启         bash deploy/server/ops.sh restart ops_web"
    ;;

  net)
    shift || true
    "$PY" "$ROOT/deploy/server/net_guard.py" --once "$@"
    ;;

  status)
    bash "$ROOT/deploy/server/status.sh"
    ;;

  help|-h|--help)
    sed -n '2,18p' "$0"
    ;;

  *)
    echo "未知子命令：$cmd（试 ops.sh help）"; exit 2
    ;;
esac
