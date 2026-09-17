"""用 program 端点的完整树（tree_3011_full.json）修复 3011 方案数据（含英语通修）。"""
from __future__ import annotations

import json
import sqlite3
import sys

sys.path.insert(0, r"f:\小蜗\scripts")
from crawl_jw_programs import extract_courses  # noqa: E402

tree = json.loads(open(r"f:\小蜗\scripts\data\tree_3011_full.json", encoding="utf-8").read())
courses = extract_courses(tree, [])
print(f"完整树提取: {len(courses)} 条")
eng = [c for c in courses if "英语" in c["category"]]
print(f"其中英语通修: {len(eng)} 条")

con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
cur = con.cursor()

# courses 索引（与 build_course_db 相同的尽力匹配逻辑）
by_code: dict[str, int] = {}
by_name: dict[str, int] = {}
for cid, code, name in cur.execute("SELECT id, code, name FROM courses").fetchall():
    if code:
        by_code.setdefault(code, cid)
        by_code.setdefault(code.lstrip("0"), cid)
    if name:
        by_name.setdefault(name, cid)

PID = 3011
cur.execute("DELETE FROM program_courses WHERE program_id=?", (PID,))
matched = 0
for c in courses:
    cid = None
    code = c.get("code", "").strip()
    cname = c.get("name", "").strip()
    if code:
        cid = by_code.get(code) or by_code.get(code.lstrip("0"))
    if cid is None and cname:
        cid = by_name.get(cname)
    if cid is None and cname:
        for row in cur.execute("SELECT id FROM courses WHERE name LIKE ? LIMIT 1", (cname + "%",)).fetchall():
            cid = row[0]
            break
    if cid:
        matched += 1
    cur.execute(
        "INSERT INTO program_courses(program_id, course_id, code, name, required, exam, credit, category, term) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (PID, cid, code, cname, c.get("required", ""), c.get("exam", ""), c.get("credit", ""), c.get("category", ""), c.get("term", "")),
    )
con.commit()

print(f"3011 重建: {len(courses)} 条入库, 匹配课程库 {matched}")
print("\n== 验证：英语通修课程 ==")
for r in cur.execute(
    "SELECT code, name, required, category FROM program_courses WHERE program_id=? AND category LIKE '%英语%' LIMIT 8",
    (PID,),
):
    print("  ", r[0], r[1], "|", r[2], "|", r[3][:40])
con.close()
