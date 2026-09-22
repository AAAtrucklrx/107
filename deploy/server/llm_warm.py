# -*- coding: utf-8 -*-
"""科大 LLM 平台智能暖启动守护（keep-warm）。

背景（2026-09-02 实测）：平台对闲置实例有冷启动（首个请求 108.6s，连续调用后
降到 6-8s）；但平台过载时也出现 503/100s 超时——此时无脑高频请求会火上浇油。

策略：
- 每 WARM_INTERVAL 秒（默认 45s）发一次极小请求（max_tokens=1），保持实例热
- 智能退避：连续 2 次失败（非 200/超时/503）→ 暂停 WARM_PAUSE 秒（默认 300s）
  （平台过载即停手，恢复后按 45s 节奏继续；成功即重置计数）
- 日志策略：失败必打；成功每 5 次打一行心跳（避免日志刷屏）

用法：
  python deploy/server/llm_warm.py            # 常驻循环
  python deploy/server/llm_warm.py --once     # 单次探测（测试/自检）
  WARM_INTERVAL=3 python deploy/server/llm_warm.py   # 测试用短间隔
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]  # 仓库根（deploy/server/llm_warm.py 上两级）
load_dotenv(ROOT / ".env")

BASE = os.environ.get("LLM_BASE_URL", "https://api.llm.ustc.edu.cn/v1").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
TIMEOUT = 25.0          # 单次请求超时（探针级，避免占用过久）
DEFAULT_INTERVAL = 45.0 # 暖启动间隔（秒）
DEFAULT_PAUSE = 300.0   # 连续失败后的退避暂停（秒）
FAIL_STREAK_LIMIT = 2   # 连续失败多少次触发退避
HEARTBEAT_EVERY = 5     # 每 N 次成功打一行心跳


def _probe() -> tuple[bool, str]:
    """发一次 min 请求；返回 (ok, 说明)。绝不打印 key。"""
    key = os.environ.get("LLM_API_KEY", "")
    if not key or len(key) < 10:
        return False, "LLM_API_KEY 未配置"
    t0 = time.time()
    try:
        r = requests.post(
            BASE + "/chat/completions",
            timeout=TIMEOUT,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 1, "temperature": 0},
        )
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            return True, f"ok {ms:.0f}ms"
        return False, f"HTTP {r.status_code} ({ms:.0f}ms)"
    except Exception as e:  # noqa: BLE001
        ms = (time.time() - t0) * 1000
        return False, f"{type(e).__name__} ({ms:.0f}ms)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="单次探测后退出")
    ap.add_argument("--interval", type=float, default=None,
                    help=f"暖启动间隔秒数（默认 {DEFAULT_INTERVAL}，环境 WARM_INTERVAL 可覆盖）")
    args = ap.parse_args()

    interval = args.interval or float(os.environ.get("WARM_INTERVAL", DEFAULT_INTERVAL))
    pause = float(os.environ.get("WARM_PAUSE", DEFAULT_PAUSE))

    def _bye(_sig, _frame) -> None:  # SIGTERM/SIGINT 立即退出
        sys.exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    ok, note = _probe()
    print(f"[{time.strftime('%F %T')}] warm 探测: {note} 目标={BASE} 模型={MODEL}", flush=True)
    if args.once:
        return 0 if ok else 1

    fail_streak = 0
    ok_count = 0
    print(f"[{time.strftime('%F %T')}] keep-warm 启动: 间隔 {interval:.0f}s, "
          f"连续 {FAIL_STREAK_LIMIT} 次失败暂停 {pause:.0f}s", flush=True)
    while True:
        ok, note = _probe()
        now = time.strftime("%F %T")
        if ok:
            fail_streak = 0
            ok_count += 1
            if ok_count % HEARTBEAT_EVERY == 0:
                print(f"[{now}] warm 心跳 {ok_count} 次正常（距上次 {ok_count * interval:.0f}s 内未踩冷启动）", flush=True)
            time.sleep(interval)
            continue
        fail_streak += 1
        print(f"[{now}] warm 失败(连续 {fail_streak}/{FAIL_STREAK_LIMIT}): {note}", flush=True)
        if fail_streak >= FAIL_STREAK_LIMIT:
            print(f"[{now}] 平台疑似过载，退避暂停 {pause:.0f}s（不加重平台负担）", flush=True)
            time.sleep(pause)
            fail_streak = 0
        else:
            time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
