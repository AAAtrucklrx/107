#!/usr/bin/env bash
# 低频探测搜索引擎冷却状态；恢复后立即执行确认态联网冒烟。循环上限约 100 分钟。
set -u
PY=/root/Desktop/小蜗/.venv/bin/python
LOG=/root/Desktop/小蜗/deploy/server/logs/search_watch.log
Q='中国科学技术大学'
for i in $(seq 1 25); do
  hits=$(curl -s -m 20 "http://127.0.0.1:8080/search?q=%E4%B8%AD%E5%9B%BD%E7%A7%91%E5%AD%A6%E6%8A%80%E6%9C%AF%E5%A4%A7%E5%AD%A6&format=json" 2>/dev/null | "$PY" -c "import json,sys;
try:
 d=json.load(sys.stdin); print(len(d.get('results',[])))
except Exception: print(0)")
  echo "[$(date '+%H:%M:%S')] loop=$i probes -> hits=$hits" >> "$LOG"
  if [ "$hits" -gt 0 ]; then
    echo "[$(date '+%H:%M:%S')] 搜索恢复，开始确认态冒烟" >> "$LOG"
    "$PY" /root/Desktop/小蜗/deploy/server/smoke_web_confirm.py >> "$LOG" 2>&1
    echo "[$(date '+%H:%M:%S')] 冒烟结束" >> "$LOG"
    exit 0
  fi
  sleep 240
done
echo "[$(date '+%H:%M:%S')] 100 分钟内未恢复（引擎封禁窗口超过预期）" >> "$LOG"
exit 3
