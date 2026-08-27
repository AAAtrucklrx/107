"""Deterministic privacy decisions that run before any retrieval provider."""

from __future__ import annotations

import re


_PERSONAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:我的|帮我查|替我查|我这学期).{0,12}(?:成绩|绩点|GPA|课表|考试|选课|学分|培养方案|日程|活动画像)",
        r"(?:成绩单|个人课表|个人培养方案|我的专业|我的年级)",
        r"\bPB\d{8}\b",
    )
)


def is_personal_query(question: str) -> bool:
    """Return True only for high-confidence personal academic intent."""

    return any(pattern.search(question) for pattern in _PERSONAL_PATTERNS)
