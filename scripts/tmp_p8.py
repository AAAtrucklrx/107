import sqlite3
con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
con.row_factory = sqlite3.Row

print("== 3011 必修课评课覆盖（经 course_id 正确关联） ==")
rows = con.execute(
    """SELECT pc.name, c.rate_count, c.rating_avg
       FROM program_courses pc
       JOIN courses c ON c.id = pc.course_id
       WHERE pc.program_id = 3011 AND pc.required = '必修'
       ORDER BY c.rate_count DESC LIMIT 25"""
).fetchall()
print(f"匹配到课程库的必修课: {len(rows)} 门")
for r in rows:
    print(f"  {r['name']}: 评课 {r['rate_count']} 条, 均分 {round(r['rating_avg'],1) if r['rating_avg'] else '-'}")

n_req = con.execute("SELECT COUNT(*) FROM program_courses WHERE program_id=3011 AND required='必修'").fetchone()[0]
matched = con.execute(
    "SELECT COUNT(*) FROM program_courses pc JOIN courses c ON c.id=pc.course_id WHERE pc.program_id=3011 AND pc.required='必修'"
).fetchone()[0]
print(f"\n必修课总数 {n_req}，关联课程库 {matched}")

print("\n== 未匹配的必修课（7.1% 缺口的实际影响） ==")
for r in con.execute(
    """SELECT pc.name, pc.code FROM program_courses pc
       WHERE pc.program_id=3011 AND pc.required='必修' AND pc.course_id IS NULL LIMIT 10"""
):
    print("  ", r["name"], r["code"])
