#!/usr/bin/env python3
"""意图分布统计（2026-09-29，只读）。

读 web_chat_runs 的意图埋点列，输出：分布、低置信度占比、分数带、按天趋势、
最近的低置信度样例（尝试解密问题原文，解不开就只显示意图与分数）。
不调用任何 LLM；只读打开数据库。

    python deploy/server/intent_stats.py [--days 7] [--low 20]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "database" / "xiaowo.db"


def _decrypt(settings, blob):
    try:
        from xiaowo_web.storage.cipher import FieldCipher
        return FieldCipher(settings.data_key).open(blob)
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="小蜗意图分布统计（只读）")
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 天（0=全部）")
    ap.add_argument("--low", type=int, default=15, help="打印多少条低置信度样例")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(web_chat_runs)")}
    if "intent" not in cols:
        print("web_chat_runs 尚无 intent 列：先启动过一次带埋点的 web 服务。")
        return 1
    where, params = "intent IS NOT NULL AND intent != ''", []
    if args.days > 0:
        where += " AND created_at >= ?"
        params.append(time.time() - args.days * 86400)
    rows = [dict(r) for r in conn.execute(
        f"SELECT run_id, intent, intent_score, intent_top3, created_at FROM web_chat_runs WHERE {where}"
        " ORDER BY created_at DESC", params)]
    total = len(rows)
    print(f"=== 已埋点运行：{total} 条" + (f"（最近 {args.days} 天）" if args.days else "（全部）") + " ===")
    if not total:
        return 0
    cnt = Counter(r["intent"] for r in rows)
    print("\n=== 意图分布 ===")
    for i, (k, v) in enumerate(cnt.most_common(), 1):
        print(f"  {i:2}. {k:<8} {v:4} 条  {v/total*100:5.1f}%")
    scores = [r["intent_score"] for r in rows if r["intent_score"] is not None]
    if scores:
        low = [s for s in scores if s < 0.5]
        bands = Counter("<0.3" if s < 0.3 else "0.3~0.4" if s < 0.4 else "0.4~0.5" if s < 0.5
                        else "0.5~0.7" if s < 0.7 else ">=0.7" for s in scores)
        print(f"\n=== 置信度（{len(scores)} 条有分数）===")
        print(f"  低置信度(<0.5)：{len(low)} 条 = {len(low)/len(scores)*100:.1f}%")
        print("  分数带：" + " · ".join(f"{k} {bands[k]}" for k in ("<0.3", "0.3~0.4", "0.4~0.5", "0.5~0.7", ">=0.7") if bands[k]))
    days = Counter(time.strftime("%m-%d", time.localtime(r["created_at"])) for r in rows)
    print("\n=== 按天 ===")
    for d, n in sorted(days.items())[-10:]:
        print(f"  {d}  {n:4} 条")
    lowrows = sorted([r for r in rows if (r["intent_score"] or 1) < 0.5], key=lambda r: r["created_at"])[-args.low:]
    if lowrows:
        print(f"\n=== 最近 {len(lowrows)} 条低置信度 ===")
        try:
            from xiaowo_web.settings import WebSettings
            settings = WebSettings.from_env()
        except Exception:  # noqa: BLE001
            settings = None
        for r in lowrows:
            q = ""
            if settings is not None:
                m = conn.execute(
                    "SELECT content_value FROM web_messages WHERE run_id = ? AND role = 'user' LIMIT 1",
                    (r["run_id"],)).fetchone()
                if m and m["content_value"]:
                    q = _decrypt(settings, m["content_value"])[:48]
            top3 = ", ".join(f"{t.get('intent')}:{t.get('score')}" for t in
                             (json.loads(r["intent_top3"]) if r["intent_top3"] else [])[:3])
            print(f"  {(r['intent_score'] or 0):.3f} [{r['intent']:<6}] {q or '(问题已过期/不可读)'}  ← {top3}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
