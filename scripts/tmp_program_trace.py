"""培养方案链路追踪：找计算机科学与技术方案 → 英语课分布 → 后端过滤逻辑。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(r"f:\小蜗\data\course_data.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("== programs 表结构 ==")
cols = [c[1] for c in con.execute("PRAGMA table_info(programs)")]
print(cols)

print("\n== 计算机科学与技术相关方案 ==")
rows = con.execute("SELECT * FROM programs WHERE name LIKE '%计算机%' OR major LIKE '%计算机%' LIMIT 10").fetchall()
for r in rows:
    print(dict(r))

print("\n== program_courses 的 category 分布（取一个计算机方案） ==")
prog = con.execute("SELECT id FROM programs WHERE name LIKE '%计算机科学与技术%' LIMIT 1").fetchone()
if prog:
    pid = prog["id"]
    print("program_id =", pid)
    for row in con.execute(
        "SELECT category, COUNT(*) n, SUM(required='必修') req FROM program_courses WHERE program_id=? GROUP BY category ORDER BY n DESC",
        (pid,),
    ):
        print(f"  {row['category']}: {row['n']} 门 (必修 {row['req']})")
    print("\n== 该方案中的英语课 ==")
    for row in con.execute(
        "SELECT code, name, required, category, term FROM program_courses WHERE program_id=? AND name LIKE '%英语%' LIMIT 10",
        (pid,),
    ):
        print("  ", dict(row))
else:
    print("（未找到名称含'计算机科学与技术'的方案，列出前 10 个方案名）")
    for r in con.execute("SELECT DISTINCT name FROM programs LIMIT 15"):
        print("  ", r[0])

con.close()
