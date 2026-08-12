"""
基于爬取的真实 catalog API 数据生成 seed SQL
"""
import json, re

data = json.load(open(r"f:\小蜗\scripts\crawl_real_data.json"))
lessons_421 = data.get("lessons_421", [])
exams_421 = data.get("exams_421", [])
gen_exams_421 = data.get("gen_exams_421", [])

# ── 1. 从真实课程中选 8 门适合大二学生的课 ──
# 选不同类型的课：数学/CS/物理/英语/通识
selected_courses = []
target_patterns = [
    ("数学分析", "MATH"),
    ("线性代数", "MATH"),
    ("数据结构", "CS"),
    ("程序设计", "CS"),
    ("力学", "PHYS"),
    ("大学物理", "PHYS"),
    ("英语", "FL"),
    ("中国近代", "HIST"),
    ("操作系统", "CS"),
    ("概率论", "MATH"),
]

for lesson in lessons_421:
    course = lesson.get("course", {})
    name_cn = course.get("cn", "")
    code = lesson.get("code", "")
    credits = lesson.get("credits", 0)
    time_text = lesson.get("dateTimePlacePersonText", {}).get("cn", "")
    
    for pattern, prefix in target_patterns:
        if pattern in name_cn and len(selected_courses) < 8:
            # 提取教师名
            teacher = ""
            # dateTimePlacePersonText 格式: "1~9,11~13周 5106 :2(8,9,10) 付邦红"
            parts = time_text.rsplit(" ", 1)
            if parts:
                teacher = parts[-1].strip()
            
            # 提取教室
            location = ""
            m = re.search(r'(\d{4})\s*:', time_text)
            if m:
                location = m.group(1)
            
            selected_courses.append({
                "code": code.replace(".01", ""),
                "name": name_cn,
                "teacher": teacher or "教师",
                "credits": credits,
                "time": time_text[:60] if time_text else "",
                "location": location,
            })
            break

# 去重
seen = set()
unique_courses = []
for c in selected_courses:
    if c["name"] not in seen:
        seen.add(c["name"])
        unique_courses.append(c)
selected_courses = unique_courses[:8]

print(f"选中 {len(selected_courses)} 门课程:")
for c in selected_courses:
    print(f"  {c['code']} {c['name']} ({c['credits']}学分) - {c['teacher']}")

# ── 2. 选一些真实考试数据 ──
sample_exams = []
for exam in exams_421[:20]:
    lesson = exam.get("lesson", {})
    course = lesson.get("course", {})
    name_cn = course.get("cn", "")
    code = lesson.get("code", "")
    exam_date = exam.get("examDate", "")
    start = exam.get("startTime", 0)
    end = exam.get("endTime", 0)
    rooms = exam.get("examRooms", [])
    room = rooms[0].get("room", "") if rooms else ""
    mode = exam.get("examMode", "")
    
    # 转换时间
    start_str = f"{start // 100:02d}:{start % 100:02d}" if start else ""
    end_str = f"{end // 100:02d}:{end % 100:02d}" if end else ""
    
    sample_exams.append({
        "course_name": name_cn,
        "course_code": code,
        "date": exam_date,
        "start": start_str,
        "end": end_str,
        "room": room,
        "mode": mode,
    })

# ── 4. 生成 SQL ──
def esc(s):
    return str(s).replace("'", "''")

sql_lines = []
sql_lines.append("-- ============================================")
sql_lines.append("-- 小蜗 种子数据 (基于真实 catalog API 数据)")
sql_lines.append("-- 生成日期: 2026-08-02")
sql_lines.append("-- ============================================")
sql_lines.append("")

# courses 表
sql_lines.append("-- 课程信息（来自 2026 春季学期真实数据）")
for i, c in enumerate(selected_courses):
    sql_lines.append(
        f"INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) "
        f"VALUES ({i+1}, '{esc(c['code'])}', '{esc(c['name'])}', '{esc(c['teacher'])}', "
        f"{c['credits']}, '{esc(c['time'])}', '{esc(c['location'])}', '2025-2026-2');"
    )
sql_lines.append("")

# 输出
sql_content = "\n".join(sql_lines)
with open(r"f:\小蜗\database\seed_data.py", "w", encoding="utf-8") as f:
    f.write('"""\n小蜗 — 种子数据模块\n基于真实 catalog API 数据生成\n"""\n\n')
    f.write(f'SEED_SQL = """\n{sql_content}\n"""\n')

print(f"\n已生成 seed_data.py ({len(sql_lines)} 行 SQL)")
print(f"  课程: {len(selected_courses)} 门")
print(f"  评课: {len(review_courses)} 门")
print(f"  教师: {len(teacher_data)} 位")
