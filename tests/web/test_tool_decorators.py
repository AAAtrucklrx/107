"""`@tool` 装饰器体检（2026-09-18 第三次踩坑后加的守卫）。

踩坑记录：把助手函数**插在** `def some_tool(...)` 那一行前面，会被上一行的 `@tool`
吃掉 —— 助手函数变成 StructuredTool，而真正的工具变成普通函数：

- `analyze_teacher` → `'StructuredTool' object is not callable`
- `get_my_program` / `plan_semester` → `'function' object has no attribute 'invoke'`

规则：助手函数必须插在 `@tool` 行**之前**。这个体检扫描 `tools/*.py` 里
`@tool` 后紧跟 `_` 开头函数的情况，让这类错误在测试阶段直接暴露。
"""

from __future__ import annotations

import re
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
_PATTERN = re.compile(r"@tool\s*\ndef\s+(_[A-Za-z0-9_]+)\s*\(")


def test_no_helper_function_is_decorated_with_tool() -> None:
    offenders: list[str] = []
    for path in sorted(_TOOLS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _PATTERN.finditer(text):
            offenders.append(f"{path.name}:{match.group(1)}")
    assert not offenders, (
        "@tool 只能装饰真正的工具函数；助手函数被装饰会顶掉工具本身："
        + "、".join(offenders)
        + "（把助手函数移到 @tool 行之前）"
    )
