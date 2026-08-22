# -*- coding: utf-8 -*-
"""render_link / links.yaml 校验（P4-2）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.link_tools import load_links, render_link  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 0)


def main() -> int:
    links = load_links()

    # 1) 清单完整性：必填字段 + https
    ok_fields = all(
        e.get("name") and e.get("url", "").startswith("https://")
        and e.get("description") and e.get("category") and e.get("scene")
        for e in links)
    check(f"清单 {len(links)} 条字段完整且均为 https", ok_fields)
    check("无重复 URL", len({e["url"] for e in links}) == len(links))

    # 2) 场景匹配
    cases = {
        "退课": "jw.ustc.edu.cn",
        "我想交学费": "revenues.ustc.edu.cn",
        "评教怎么弄": "tqm.ustc.edu.cn",
        "去图书馆借书": "lib.ustc.edu.cn",
        "四六级在哪报名": "cet-bm.neea.edu.cn",
        "找实习": "career.ustc.edu.cn",
        "密码忘了": "id.ustc.edu.cn",
    }
    for scene, want_host in cases.items():
        r = render_link.invoke({"scene": scene})
        check(f"场景「{scene}」→ {want_host}",
              r.get("found") and want_host in r.get("url", ""), str(r)[:120])

    # 3) 无匹配：如实未找到（不编造）
    r = render_link.invoke({"scene": "量子波动速读报名"})
    check("无匹配场景如实 found=false", r.get("found") is False and "禁止编造" in r.get("note", ""))
    r = render_link.invoke({"scene": ""})
    check("空场景提示补全", r.get("found") is False)

    # 4) 注册表纳入
    from agents.tool_registry import _build_tool_registry
    check("注册表含 render_link", "render_link" in _build_tool_registry())

    print(f"\n结果: 通过 {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
