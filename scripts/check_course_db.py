# -*- coding: utf-8 -*-
"""选课推荐数据库 9 项检查。

用法: python scripts/check_course_db.py [--db data/course_data.db]
退出码: 0=全部通过, 1=存在失败项
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="course_data.db 完整性检查")
    ap.add_argument("--db", default=str(PROJECT_ROOT / "data" / "course_data.db"))
    args = ap.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"[FAIL] 数据库不存在: {db}")
        sys.exit(1)
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()

    # 1. 8 表齐全
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    expected = {"courses", "reviews", "course_rates", "course_terms", "teachers",
                "course_teachers", "programs", "program_courses"}
    missing = expected - tables
    check("表结构齐全", not missing, f"缺失: {sorted(missing)}" if missing else f"{len(tables)} 张表")

    # 2. 核心行数
    n_courses = cur.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    n_reviews = cur.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    check("课程/评论行数", n_courses > 0 and n_reviews > 0, f"courses={n_courses} reviews={n_reviews}")

    # 3. course_rates 覆盖（有评论的课程都有聚合行）
    no_rate = cur.execute(
        "SELECT COUNT(*) FROM courses c WHERE c.rate_count > 0 "
        "AND NOT EXISTS (SELECT 1 FROM course_rates r WHERE r.course_id = c.id)"
    ).fetchone()[0]
    check("预聚合全覆盖", no_rate == 0, f"缺聚合行课程 {no_rate}")

    # 4. 预聚合对账: rating_count == 已评分评论计数（stars>0, 未评分不计入均分）
    mismatch = cur.execute(
        "SELECT COUNT(*) FROM course_rates r WHERE r.rating_count != "
        "(SELECT COUNT(*) FROM reviews v WHERE v.course_id = r.course_id AND v.stars > 0)"
    ).fetchone()[0]
    check("评分样本量对账", mismatch == 0, f"不一致 {mismatch} 门")

    # 5. 均分自洽: |sum/count - avg| < 0.001
    bad_avg = cur.execute(
        "SELECT COUNT(*) FROM course_rates WHERE rating_count > 0 AND "
        "ABS(rating_sum / rating_count - rating_avg) > 0.001"
    ).fetchone()[0]
    check("均分自洽", bad_avg == 0, f"异常 {bad_avg} 门")

    # 6. 学期无重复 (course_id, term)
    dup_terms = cur.execute(
        "SELECT COUNT(*) FROM (SELECT course_id, term FROM course_terms GROUP BY course_id, term HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    check("学期无重复", dup_terms == 0, f"重复组 {dup_terms}")

    # 7. 孤儿引用
    orphans = 0
    for tbl, col in [("reviews", "course_id"), ("course_rates", "course_id"),
                     ("course_terms", "course_id"), ("course_teachers", "course_id"),
                     ("program_courses", "course_id")]:
        orphans += cur.execute(
            f"SELECT COUNT(*) FROM {tbl} t WHERE t.{col} IS NOT NULL AND "
            f"NOT EXISTS (SELECT 1 FROM courses c WHERE c.id = t.{col})"
        ).fetchone()[0]
    orphans += cur.execute(
        "SELECT COUNT(*) FROM course_teachers t WHERE NOT EXISTS "
        "(SELECT 1 FROM teachers x WHERE x.id = t.teacher_id)"
    ).fetchone()[0]
    check("无孤儿引用", orphans == 0, f"孤儿 {orphans}")

    # 8. icourse_id 可溯源（reviews.icourse_id 在 courses.icourse_ids JSON 中）
    untraceable = 0
    for row in cur.execute("SELECT id, icourse_ids FROM courses").fetchall():
        ids = json.loads(row[1] or "[]")
        n = cur.execute(
            "SELECT COUNT(*) FROM reviews WHERE course_id = ? AND icourse_id NOT IN (%s)"
            % ",".join("?" * max(len(ids), 1)),  # type: ignore
            [row[0]] + (ids or [-1]),
        ).fetchone()[0]
        untraceable += n
    check("icourse_id 可溯源", untraceable == 0, f"无法溯源 {untraceable} 条")

    # 9. 数据质量: 星级合法（0=未评分, 或 0.5-5 步进 0.5）+ 维度映射在合法范围
    n_star0 = cur.execute(
        "SELECT COUNT(*) FROM reviews WHERE stars < 0 OR stars > 5 OR ABS(stars*2 - ROUND(stars*2)) > 0.0001"
    ).fetchone()[0]
    bad_dims = cur.execute(
        "SELECT COUNT(*) FROM course_rates WHERE diff_avg > 10.001 OR hw_avg > 10.001 "
        "OR score_avg > 10.001 OR gain_avg > 10.001"
    ).fetchone()[0]
    n_teachers = cur.execute("SELECT COUNT(*) FROM teachers").fetchone()[0]
    n_ct = cur.execute("SELECT COUNT(*) FROM course_teachers").fetchone()[0]
    check("数据质量", n_star0 == 0 and bad_dims == 0,
          f"星级越界 {n_star0}, 维度越界 {bad_dims}, teachers={n_teachers}, course_teachers={n_ct}")

    conn.close()
    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\n结果: {len(CHECKS) - len(failed)}/{len(CHECKS)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
