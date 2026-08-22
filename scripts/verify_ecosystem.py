# -*- coding: utf-8 -*-
"""生态工具注册协议 v1 自检（P4-1）。

断言：加载/校验/拒载、注册表合并、echo 直调（含 source 兜底与 ctx）、
Spec 元数据、_TOOL_LIST 纳入、摘要署名分支。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ecosystem import (  # noqa: E402
    ecosystem_specs, load_ecosystem_tools, reload_ecosystem_tools,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def main() -> int:
    tools, specs = reload_ecosystem_tools()

    # 1) echo 加载成功且 Spec 元数据齐全
    check("echo 工具已加载", "eco:echo" in tools)
    spec = next((s for s in specs if s["name"] == "eco:echo"), None)
    check("echo Spec 元数据可读", spec is not None and spec["provider"] and spec["permission"] == "read_only")

    # 2) 注册表合并：内置 + 生态
    from agents.tool_registry import _build_tool_registry
    registry = _build_tool_registry()
    check("注册表合并（内置 + eco:echo）",
          "eco:echo" in registry and "query_schedule" in registry,
          f"共 {len(registry)} 工具")

    # 3) echo 直调（act 层 func(**args) 形态）
    r = registry["eco:echo"](message="协议自检")
    check("echo 直调成功", r.get("echo") == "协议自检", str(r))
    check("echo 返回含 source 署名", "第三方" in str(r.get("source", "")))
    check("echo 空参数返回 error 不抛异常",
          registry["eco:echo"]().get("error", "") != "")

    # 4) 坏 Spec 拒载：临时写一个缺字段且无 eco: 前缀的 Spec，重扫后不应出现、不炸
    eco_dir = Path(__file__).resolve().parents[1] / "tools" / "ecosystem"
    bad = eco_dir / "_bad.spec.yaml"
    bad.write_text("name: not_prefixed\ndisplay_name: 坏样例\n", encoding="utf-8")
    try:
        tools2, _ = reload_ecosystem_tools()
        check("坏 Spec 拒载且不影响其他工具", "not_prefixed" not in tools2 and "eco:echo" in tools2)
    finally:
        bad.unlink()
    reload_ecosystem_tools()  # 恢复干净缓存

    # 5) _TOOL_LIST 纳入生态工具
    from agents.qa.nodes import _TOOL_LIST
    check("_TOOL_LIST 含 eco:echo", "eco:echo" in _TOOL_LIST)

    # 6) 摘要署名分支
    from agents.qa.nodes import _build_tool_summary
    summary = _build_tool_summary([{"tool": "eco:echo", "status": "done", "result": r}])
    check("摘要含第三方署名与回显内容",
          "第三方工具" in summary and "协议自检" in summary, summary[:200])
    err_summary = _build_tool_summary([{"tool": "eco:echo", "status": "done",
                                        "result": {"error": "boom"}}])
    check("摘要失败分支如实呈现", "boom" in err_summary)

    print(f"\n结果: 通过 {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
