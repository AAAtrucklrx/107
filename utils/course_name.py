"""
课程名归一化共享实现（Phase 2c 收敛：原 advisor_tools / course_tools / program_tools 三处
各自实现且语义不一——advisor 剥引号但只去半角空格，course/program 去全角空格但不剥引号，
导致已修课程匹配与候选池过滤口径分歧）。

统一口径（并集，最激进归一化）：
- 全角括号 → 半角
- 去所有括号、中文/英文引号、所有空白字符
- ASCII 大写
使 '数学分析 (B1)'、'数学分析（B1）'、'数学分析 B1'、'"科学与社会"研讨课' 等
变体全部收敛到同一键。
"""
from __future__ import annotations


def norm_course_name(name: str) -> str:
    s = str(name or "").translate(str.maketrans("（）", "()"))
    s = s.replace("(", "").replace(")", "")
    for ch in "\"'“”‘’":
        s = s.replace(ch, "")
    return "".join(ch for ch in s if not ch.isspace()).upper()
