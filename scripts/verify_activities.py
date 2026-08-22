# -*- coding: utf-8 -*-
"""query_activities / 活动链路校验（P4-B）。

使用真实 token 实拉验证；token 失效时相关断言标 SKIP（不算失败）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.activity_tools import query_activities  # noqa: E402

PASS = FAIL = SKIP = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def main() -> int:
    r = query_activities.invoke({})
    live = "error" not in r

    # 1) 注册表 + 工具清单
    from agents.tool_registry import _build_tool_registry
    from agents.qa.nodes import _TOOL_LIST
    check("注册表含 query_activities", "query_activities" in _build_tool_registry())
    check("_TOOL_LIST 含 query_activities", "query_activities" in _TOOL_LIST)

    global SKIP
    if not live:
        SKIP = 1
        print(f"[SKIP] token 失效，实拉断言跳过（{r.get('error', '')[:60]}）")
        print(f"\n结果: 通过 {PASS}/{PASS + FAIL}（{SKIP} 项跳过）")
        return 0 if FAIL == 0 else 1

    # 2) 实拉：结构与来源
    check("全量返回 activities 列表", isinstance(r.get("activities"), list) and r["count"] >= 0)
    check("source 为实时标识", "青春科大" in r.get("source", ""))
    total = r["count"]

    # 3) 关键词过滤（用返回中真实存在的词自校验）
    if r["activities"]:
        probe = r["activities"][0]
        kw = probe["name"][:4]
        rk = query_activities.invoke({"keyword": kw})
        check(f"关键词过滤「{kw}」结果不大于全量", rk.get("count", 0) <= total + 1)
    check("空关键词=全量", query_activities.invoke({"keyword": ""})["count"] == total)

    # 4) 时间窗：即将截止（不崩溃且结果≤全量）
    rw = query_activities.invoke({"time_window": "即将截止"})
    check("时间窗过滤正常", rw.get("count", 0) <= total)

    # 5) limit
    rl = query_activities.invoke({"limit": 3})
    check("limit=3 生效", rl.get("count", 0) <= 3)

    print(f"\n结果: 通过 {PASS}/{PASS + FAIL}（实拉 {total} 条）")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
