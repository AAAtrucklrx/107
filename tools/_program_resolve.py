"""
方案定位共享实现（Phase 2b 收敛）。

历史：advisor_tools 与 program_tools 各自复刻了一份 _resolve_program，且行为不一致
（advisor 带英才班/辅修优先级，program 无），同一专业可能在推荐与方案页定位到不同方案。
本模块为唯一实现，两处工具薄封装调用，保证口径一致。
"""
from __future__ import annotations

import re


def parse_grade_key(grade: str) -> int:
    """年级 → 可排序整数（"2024级"→2024，"大二"→无法解析返回 0）。"""
    m = re.match(r"\D*(\d{4})\D*", str(grade or ""))
    return int(m.group(1)) if m else 0


def prog_priority(r) -> int:
    """方案类型优先级：普通专业方案 0；英才班/带括号特殊方案 1；辅修 2。

    同年级多方案命中（普通班 vs 英才班/少年班等）时优先普通专业方案，
    避免给普通班学生推荐英才班专属课程（如量子物理、并行计算A 等）。"""
    name = r["name"] or ""
    if "辅修" in name:
        return 2
    if "英才班" in name or "（" in name or "(" in name:
        return 1
    return 0


def resolve_program(conn, major: str | None, grade: str | None = None) -> dict | None:
    """全量库方案定位：同年级 → 最近低年级 → 最新；同年级内普通专业方案优先。

    匹配顺序：方案名 LIKE 精确优先（college LIKE 会误伤，如 major="人工智能" 命中
    人工智能与数据科学学院的数据科学方案），无命中才回退 college。

    Returns:
        {"id", "name", "college", "grade", ...} 或 None
    """
    if not major:
        return None
    rows = conn.execute(
        "SELECT * FROM programs WHERE name LIKE ? ORDER BY grade DESC",
        (f"%{major}%",),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT * FROM programs WHERE college LIKE ? ORDER BY grade DESC",
            (f"%{major}%",),
        ).fetchall()
    if not rows:
        return None

    target = parse_grade_key(grade)

    def _sort_key(r):
        g = parse_grade_key(r["grade"])
        if target:
            diff = g - target
            bucket = 0 if diff == 0 else (1 if diff < 0 else 2)
        else:
            bucket = 0  # 无年级信息: 不按年级分桶, 普通方案优先 + 最新在前
        return (bucket, prog_priority(r), -g)

    rows = sorted(rows, key=_sort_key)
    return dict(rows[0])
