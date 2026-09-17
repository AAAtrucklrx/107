import sqlite3
con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
con.row_factory = sqlite3.Row

def cats(pid):
    return {r["category"] for r in con.execute("SELECT DISTINCT category FROM program_courses WHERE program_id=?", (pid,))}

c2025 = cats(3011)   # 2025 计算机
c2024 = cats(2763)   # 2024 计算机
c2023 = cats(2612)   # 2023 计算机
print("2024 级有而 2025 级没有的分组:")
for c in sorted(c2024 - c2025):
    print("  -", c)
print("2023 级有而 2025 级没有的分组:")
for c in sorted(c2023 - c2025):
    print("  -", c)
print("\n2024 级计算机的外语类课:")
for r in con.execute("SELECT code, name, category FROM program_courses WHERE program_id=2763 AND (name LIKE '%英语%' OR category LIKE '%外语%') LIMIT 10"):
    print("  ", dict(r))

print("\n== 全库统计：含'外语'类课程的方案数 ==")
n_total = con.execute("SELECT COUNT(DISTINCT program_id) FROM program_courses").fetchone()[0]
n_waiyu = con.execute("SELECT COUNT(DISTINCT program_id) FROM program_courses WHERE category LIKE '%外语%'").fetchone()[0]
n_yingyu = con.execute("SELECT COUNT(DISTINCT program_id) FROM program_courses WHERE name LIKE '%英语%'").fetchone()[0]
print(f"有课程的方案总数: {n_total}；含'外语'分组的: {n_waiyu}；含'英语'课名的: {n_yingyu}")

print("\n== 全库 program_courses 的 category 里含'外语'的种类 ==")
for r in con.execute("SELECT DISTINCT category FROM program_courses WHERE category LIKE '%外语%' LIMIT 10"):
    print("  ", r[0])
