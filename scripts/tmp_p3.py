import sqlite3
con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
con.row_factory = sqlite3.Row
pid = 3011
print("== 3011 的 category 分布 ==")
for row in con.execute("SELECT category, COUNT(*) n, SUM(required='必修') req FROM program_courses WHERE program_id=? GROUP BY category ORDER BY n DESC", (pid,)):
    print(f"  {row['category']}: {row['n']} 门 (必修 {row['req']})")
print("\n== 3011 含'英语'的课 ==")
for row in con.execute("SELECT code, name, required, category, term, credit FROM program_courses WHERE program_id=? AND (name LIKE '%英语%' OR code LIKE '%FL%' OR code LIKE '%018%') LIMIT 15", (pid,)):
    print("  ", dict(row))
print("\n== 3011 通修/公共类课（英语通常在这里） ==")
for row in con.execute("SELECT code, name, required, category, term FROM program_courses WHERE program_id=? AND (category LIKE '%通修%' OR category LIKE '%公共%' OR category LIKE '%核心通识%') LIMIT 15", (pid,)):
    print("  ", dict(row))
