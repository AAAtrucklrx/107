import sqlite3
con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
con.row_factory = sqlite3.Row
print("== 计算机相关方案 ==")
rows = con.execute("SELECT id, name, college, grade FROM programs WHERE name LIKE '%计算机%' LIMIT 10").fetchall()
for r in rows:
    print(dict(r))
print("\n== 方案名里含 2025/2026 级计算机 ==")
rows = con.execute("SELECT id, name, grade FROM programs WHERE name LIKE '%计算机%' AND (grade LIKE '%2025%' OR name LIKE '%2025%') LIMIT 10").fetchall()
for r in rows:
    print(dict(r))
