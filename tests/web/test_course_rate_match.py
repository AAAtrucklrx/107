"""方案课程 ↔ 评课库评分映射：前缀码命中残缺行的修复（2026-09-22）。

用户实测（09-19 线上真机回答）：推荐表里 `计算机组成原理 | 4.0 | 2春·必修 | 均分 1.0 · 1条`，
评课原文还写着「还有未知老师的计算机组成原理课程？出 bug 了吧」——而那门课的真实评课页是
8.0/52 条 与 9.4/31 条。

根因：教务个人方案树给的是**6 位前缀码**（`011145`），`_find_rated_course` 先按课程号
精确匹配，恰好命中评课库里一行同名残缺码数据（`011145`, n=1, avg=1.0）；真实页
（`01114501` / `01114502`）因为码不同被完全忽略。

修法：① 课程号精确命中要求样本量 ≥ `_MIN_CODE_SAMPLE`；② 否则用方案课程号作**前缀**
+ 同名取样本量最大者（教务 6 位 ↔ 评课 9 位）；③ 再回退同名取样本量最大者。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import tools.advisor_tools as advisor_tools
from tools.advisor_tools import _MIN_CODE_SAMPLE, _find_rated_course

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema_course.sql"


def _db(tmp_path: Path, rows, rates):
    """rows: (id, name, code, credit)；rates: (course_id, count, avg)。"""
    path = tmp_path / "course_data.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    rate_by_id = {cid: (cnt, avg) for (cid, cnt, avg) in rates}
    conn.executemany(
        "INSERT INTO courses(id, name, dept, code, credit, icourse_ids, rating_avg, rate_count)"
        " VALUES(?,?,?,?,?, '[]', ?, ?)",
        [(i, n, "计算机科学与技术学院", c, cr, rate_by_id[i][1], rate_by_id[i][0])
         for (i, n, c, cr) in rows],
    )
    conn.executemany(
        "INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg, dims_dist)"
        " VALUES(?,?,?,?, '{}')",
        [(cid, cnt * avg, cnt, avg) for (cid, cnt, avg) in rates],
    )
    conn.commit()
    conn.close()

    def _fake_cdb():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return _fake_cdb


# 「计算机组成原理」的真实形态：一行残缺码 n=1，两个完整码 52 / 31 条
JUNK_AND_REAL_ROWS = [
    (1, "计算机组成原理", "011145", 4.0),      # 残缺码，单条差评（1.0）
    (2, "计算机组成原理", "01114501", 4.0),    # 真实页，8.0 / 52
    (3, "计算机组成原理", "01114502", 4.0),    # 真实页，9.4 / 31
]
JUNK_AND_REAL_RATES = [(1, 1, 1.0), (2, 52, 8.0), (3, 31, 9.4)]


def test_prefix_code_does_not_hit_single_sample_junk_row(tmp_path, monkeypatch) -> None:
    """核心回归：方案 code=011145 不得再命中 n=1 的 1.0 分残缺行。"""
    monkeypatch.setattr(advisor_tools, "_cdb", _db(tmp_path, JUNK_AND_REAL_ROWS, JUNK_AND_REAL_RATES))
    conn = advisor_tools._cdb()
    try:
        row = _find_rated_course(conn, {"code": "011145", "name": "计算机组成原理"})
    finally:
        conn.close()
    assert row is not None
    assert row["rating_count"] == 52, "应取同前缀同名的最大样本页"
    assert round(row["rating_avg"], 1) == 8.0
    assert row["code"] == "01114501"


def test_exact_code_with_enough_samples_still_wins(tmp_path, monkeypatch) -> None:
    """边界：课程号精确命中且样本充足时，仍按课程号精确匹配（不被前缀步抢走）。"""
    rows = [
        (1, "图论", "CS3001", 3.0),
        (2, "图论", "CS300101", 3.0),   # 同名前缀、样本更多
    ]
    rates = [(1, 8, 9.0), (2, 40, 5.0)]
    monkeypatch.setattr(advisor_tools, "_cdb", _db(tmp_path, rows, rates))
    conn = advisor_tools._cdb()
    try:
        row = _find_rated_course(conn, {"code": "CS3001", "name": "图论"})
    finally:
        conn.close()
    assert row["code"] == "CS3001" and row["rating_count"] == 8


def test_tiny_sample_exact_code_falls_through_to_name_match(tmp_path, monkeypatch) -> None:
    """边界：连精确码都只有 1 条样本时，回退同名最大样本（而不是把 1 条当整门课）。"""
    rows = [
        (1, "量子物理", "PHYS1010", 3.0),
        (2, "量子物理", "PHYS101002", 3.0),
    ]
    rates = [(1, 1, 10.0), (2, 62, 9.8)]
    monkeypatch.setattr(advisor_tools, "_cdb", _db(tmp_path, rows, rates))
    conn = advisor_tools._cdb()
    try:
        row = _find_rated_course(conn, {"code": "PHYS1010", "name": "量子物理"})
    finally:
        conn.close()
    assert row["code"] == "PHYS101002" and row["rating_count"] == 62


def test_name_match_still_used_when_code_absent(tmp_path, monkeypatch) -> None:
    """无课程号（或码太长/太短）时仍是同名最大样本。"""
    rows = [(1, "数理方程B", "", 2.0), (2, "数理方程B", "001549", 2.0)]
    rates = [(1, 3, 7.0), (2, 32, 9.8)]
    monkeypatch.setattr(advisor_tools, "_cdb", _db(tmp_path, rows, rates))
    conn = advisor_tools._cdb()
    try:
        row = _find_rated_course(conn, {"code": "", "name": "数理方程B"})
    finally:
        conn.close()
    assert row["code"] == "001549" and row["rating_count"] == 32


def test_min_code_sample_constant_documented() -> None:
    assert _MIN_CODE_SAMPLE == 3
