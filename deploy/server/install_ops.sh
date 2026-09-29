#!/usr/bin/env bash
# 小蜗运维加固安装（2026-09-29）——可重复执行，幂等
#
# 补齐三个缺口：
#   ① 开机自启：/etc/cron.d/xiaowo-autostart（@reboot 等 30s 跑 start_all.sh）+ 每日一次对账（自愈缺失的循环服务）
#   ② 日志轮转：/etc/logrotate.d/xiaowo（每日 / 保留 14 天 / copytruncate，适配 nohup 常驻进程）
#   ③ 出网巡检：校验 net_guard 已加入 start_all/stop_all/status（由仓库脚本提供，本脚本只做检查）
#
# 用法：bash deploy/server/install_ops.sh            # 安装/更新
#       bash deploy/server/install_ops.sh --check    # 只检查现状，不写系统文件
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$ROOT/deploy/server/logs"
LOGROTATE_CONF="/etc/logrotate.d/xiaowo"
CRON_CONF="/etc/cron.d/xiaowo-autostart"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

echo "部署目录：$ROOT"
[ -d "$LOG_DIR" ] || mkdir -p "$LOG_DIR"

# ── ① 开机自启（cron，不动现有 nohup 体系）────────────────────────────
cron_body="# 小蜗开机自启与每日对账（由 deploy/server/install_ops.sh 安装）
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
@reboot root sleep 30; bash $ROOT/deploy/server/start_all.sh >> $LOG_DIR/autostart.log 2>&1
30 6 * * * root bash $ROOT/deploy/server/start_all.sh >> $LOG_DIR/autostart.log 2>&1
"
if [ "$CHECK_ONLY" = "1" ]; then
  echo "[check] 自启配置：$([ -f "$CRON_CONF" ] && echo 已安装 || echo '未安装')"
else
  printf '%s' "$cron_body" > "$CRON_CONF" && chmod 644 "$CRON_CONF" && echo "[ok] 已写入 $CRON_CONF（@reboot + 每日 06:30 对账）"
fi

# ── ② 日志轮转 ───────────────────────────────────────────────────────
logrotate_body="$LOG_DIR/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su root root
}
# 备份/自启日志体量小，同样纳入
$LOG_DIR/../logs/*.out $LOG_DIR/*.out {
    monthly
    rotate 3
    compress
    missingok
    notifempty
    copytruncate
}
"
if [ "$CHECK_ONLY" = "1" ]; then
  echo "[check] 日志轮转：$([ -f "$LOGROTATE_CONF" ] && echo 已安装 || echo '未安装')"
else
  printf '%s' "$logrotate_body" > "$LOGROTATE_CONF" && chmod 644 "$LOGROTATE_CONF" && echo "[ok] 已写入 $LOGROTATE_CONF（daily / 14 天 / copytruncate）"
  if command -v logrotate >/dev/null 2>&1; then
    logrotate -d "$LOGROTATE_CONF" >/tmp/logrotate_check.txt 2>&1 && echo "[ok] logrotate 配置自检通过" || { echo "[warn] logrotate 自检有问题："; tail -6 /tmp/logrotate_check.txt; }
  else
    echo "[warn] 未找到 logrotate，配置已写但不会自动执行"
  fi
fi

# ── ③ 出网巡检（检查仓库脚本是否已接入服务管理）───────────────────────
for f in start_all.sh stop_all.sh status.sh; do
  if grep -q "net_guard" "$ROOT/deploy/server/$f" 2>/dev/null; then
    echo "[ok] $f 已接入 net_guard"
  else
    echo "[warn] $f 未接入 net_guard（请更新仓库脚本后再执行本脚本）"
  fi
done
[ -f "$ROOT/deploy/server/net_guard.py" ] && echo "[ok] net_guard.py 存在" || echo "[warn] 缺少 net_guard.py"

# ── ④ cron 服务状态 ─────────────────────────────────────────────────
if systemctl is-active cron >/dev/null 2>&1; then
  echo "[ok] cron 正在运行（自启/对账依赖它）"
else
  echo "[warn] cron 未运行：systemctl enable --now cron"
fi

echo
echo "完成。自检：bash deploy/server/ops.sh --check 不适用，请运行：bash deploy/server/ops.sh"
echo "验证自启：cat $CRON_CONF ; 验证轮转：logrotate -d $LOGROTATE_CONF | tail -5"
