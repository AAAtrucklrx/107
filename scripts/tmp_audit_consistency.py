"""数据一致性全面审计：培养方案/课程库/演示学生数据的对齐情况。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
con.row_factory = sqlite3.Row

print("== 1. program_courses → courses 关联完整性 ==")
total = con.execute("SELECT COUNT(*) FROM program_courses").fetchone()[0]
unmatched = con.execute("SELECT COUNT(*) FROM program_courses WHERE course_id IS NULL").fetchone()[0]
print(f"program_courses 总数 {total}，未匹配 course_id: {unmatched} ({unmatched/total*100:.1f}%)")

print("\n== 2. 未匹配课程样本（这些课无法关联评课/推荐） ==")
for r in con.execute(
    "SELECT code, name, category FROM program_courses WHERE course_id IS NULL LIMIT 8"
):
    print("  ", r["code"], r["name"], "|", r["category"][:30])

print("\n== 3. program 课程在 courses 库中查不到（按 code/name） ==")
missing = con.execute(
    """SELECT COUNT(*) FROM program_courses pc
       WHERE NOT EXISTS (SELECT 1 FROM courses c WHERE c.code = pc.code)"""
).fetchone()[0]
print(f"code 不在课程库的方案课: {missing}/{total}")

print("\n== 4. courses 表 5667 门 vs 方案引用的课程 ==")
used = con.execute(
    "SELECT COUNT(DISTINCT pc.code) FROM program_courses pc WHERE EXISTS (SELECT 1 FROM courses c WHERE c.code=pc.code)"
).fetchone()[0]
print(f"方案引用且课程库有的 code 数: {used}")

print("\n== 5. 评课覆盖 ==")
rated = con.execute("SELECT COUNT(*) FROM courses WHERE rate_count > 0").fetchone()[0]
print(f"有评课数据的课程: {rated}/5667")

print("\n== 6. course_terms 学期覆盖样本 ==")
n_terms = con.execute("SELECT COUNT(DISTINCT term) FROM course_terms").fetchone()[0]
print(f"course_terms 不同学期值: {n_terms}")

print("\n== 7. 2025 计算机方案(3011) 的课在课程库的评课覆盖 ==")
rows = con.execute(
    """SELECT pc.name, c.rate_count FROM program_courses pc
       LEFT JOIN courses c ON c.code = pc.code
       WHERE pc.program_id = 3011 AND pc.required='必修' LIMIT 20"""
).fetchall()
for r in rows:
    rc = r["rate_count"] if r["rate_count"] is not None else "未匹配"
    print(f"  {r['name']}: 评课 {rc}")
con.close()

print("\n== 8. 演示学生方案匹配 ==")
con2 = sqlite3.connect(r"f:\小蜗\database\xiaowo.db")
con2.row_factory = sqlite3.Row
prof = con2.execute("SELECT * FROM student_courses WHERE student_id='PB25111691' LIMIT 1").fetchone()
if prof:
    print("演示学生课表字段:", list(prof.keys()))
con2.close()
