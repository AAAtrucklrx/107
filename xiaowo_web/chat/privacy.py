"""Deterministic privacy decisions that run before any retrieval provider."""

from __future__ import annotations

import re

from xiaowo_web.privacy_patterns import STUDENT_ID_PATTERN

_PERSONAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:我的|帮我查|替我查|我这学期).{0,12}(?:成绩|绩点|GPA|课表|考试|选课|学分|培养方案|日程|活动画像)",
        r"(?:成绩单|个人课表|个人培养方案|我的专业|我的年级)",
        # 学号名单与边界统一到 privacy_patterns：原先只认 PB，研究生学号整类漏判
        STUDENT_ID_PATTERN,
    )
)


def is_personal_query(question: str) -> bool:
    """Return True only for high-confidence personal academic intent."""

    return any(pattern.search(question) for pattern in _PERSONAL_PATTERNS)
