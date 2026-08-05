"""
从完整课程列表中选课，覆盖多学科
"""
import json, requests, random

BASE = "https://catalog.ustc.edu.cn"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# 爬取完整课程列表
print("爬取 2026 春季完整课程列表...")
resp = requests.get(f"{BASE}/api/teach/lesson/list-for-teach/421", headers=HEADERS, timeout=30)
all_lessons = resp.json()
print(f"  总课程数: {len(all_lessons)}")

# 目标：选 8 门覆盖不同学科
targets = {
    "数学分析": None,
    "线性代数": None,
    "数据结构": None,
    "程序设计": None,
    "大学物理": None,
    "英语": None,
    "操作系统": None,
    "概率论": None,
    "离散数学": None,
    "计算机网络": None,
    "中国近现代": None,
    "力学": None,
}

for lesson in all_lessons:
    course = lesson.get("course", {})
    name_cn = course.get("cn", "")
    code = lesson.get("code", "").split(".")[0]
    credits = lesson.get("credits", 0)
    time_text = lesson.get("dateTimePlacePersonText", {}).get("cn", "")
    
    for keyword in targets:
        if keyword in name_cn and targets[keyword] is None:
            # 提取教师
            parts = time_text.rsplit(" ", 1) if time_text else []
            teacher = parts[-1].strip() if parts else "教师"
            # 提取教室
            import re
            m = re.search(r'(\d{4})\s*:', time_text)
            location = m.group(1) if m else ""
            
            targets[keyword] = {
                "code": code,
                "name": name_cn,
                "teacher": teacher,
                "credits": credits,
                "time": time_text[:60] if time_text else "",
                "location": location,
            }
            break

# 收集选中的课
selected = [v for v in targets.values() if v is not None][:8]
print(f"\n选中 {len(selected)} 门课程:")
for c in selected:
    print(f"  {c['code']} {c['name']} ({c['credits']}学分) - {c['teacher']}")

# 生成成绩
random.seed(42)
grades = []
for c in selected[:6]:
    score = random.randint(72, 95)
    if score >= 90: gp = 4.0
    elif score >= 85: gp = 3.7
    elif score >= 82: gp = 3.3
    elif score >= 78: gp = 3.0
    elif score >= 75: gp = 2.7
    elif score >= 72: gp = 2.3
    else: gp = 1.0
    grades.append({"name": c["name"], "credits": c["credits"], "score": score, "grade_point": gp})

# 生成 SQL
STUDENT = "PB20240001"
def esc(s): return str(s).replace("'", "''").replace('\n', ' ').replace('\r', '').strip()

lines = []
lines.append('"""')
lines.append('小蜗 — 种子数据模块')
lines.append('基于真实 catalog API 数据生成 (2026 春季学期)')
lines.append('"""')
lines.append('')
lines.append(f'DEMO_STUDENT_ID = "{STUDENT}"')
lines.append('')
lines.append('SEED_SQL = """')
lines.append('-- ============================================')
lines.append('-- 小蜗 种子数据 (基于真实 catalog API 数据)')
lines.append('-- ============================================')
lines.append('')

# courses
lines.append('-- 课程信息')
for i, c in enumerate(selected):
    lines.append(
        f"INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) "
        f"VALUES ({i+1}, '{esc(c['code'])}', '{esc(c['name'])}', '{esc(c['teacher'])}', "
        f"{c['credits']}, '{esc(c['time'])}', '{esc(c['location'])}', '2025-2026-2');"
    )
lines.append('')

# student_courses
lines.append(f'-- 演示学生 {STUDENT} 课表')
for i, c in enumerate(selected):
    lines.append(
        f"INSERT OR REPLACE INTO student_courses (id, student_id, course_code, course_name, teacher, credits, time, location, semester) "
        f"VALUES ({i+1}, '{STUDENT}', '{esc(c['code'])}', '{esc(c['name'])}', '{esc(c['teacher'])}', "
        f"{c['credits']}, '{esc(c['time'])}', '{esc(c['location'])}', '2025-2026-2');"
    )
lines.append('')

# student_grades
lines.append(f'-- 演示学生成绩 (上学期)')
for i, g in enumerate(grades):
    lines.append(
        f"INSERT OR REPLACE INTO student_grades (id, student_id, semester, course_name, credits, score, grade_point) "
        f"VALUES ({i+1}, '{STUDENT}', '2025-2026-1', '{esc(g['name'])}', {g['credits']}, {g['score']}, {g['grade_point']});"
    )
lines.append('')

# course_reviews
lines.append('-- 评课社区数据')
reviews = [
    ("CS2001", "机器学习导论", "王教授", 8.7, 6.5, 5.0, "给分好", "人工智能,机器学习", 45, "内容充实，讲课清晰"),
    ("CS2002", "算法设计与分析", "李教授", 8.5, 7.2, 6.0, "给分一般", "算法,数据结构", 38, "核心课，难度较高但收获大"),
    ("CS2003", "操作系统", "周教授", 7.8, 7.5, 7.0, "给分一般", "系统,C语言", 52, "硬核课程，实验量大"),
    ("CS2004", "数据库系统", "赵教授", 8.2, 5.5, 4.5, "给分好", "数据库,SQL", 30, "实用性强，讲课清楚"),
    ("MATH2001", "概率论与数理统计", "孙教授", 8.9, 6.0, 5.5, "给分好", "数学,统计", 60, "基础课，讲得很好"),
    ("ENG2001", "学术英语写作", "陈教授", 7.5, 4.5, 5.0, "给分好", "英语,写作", 22, "对写论文有帮助"),
    ("PHYS2001", "大学物理B", "张教授", 8.0, 7.8, 7.5, "给分一般", "物理,实验", 35, "难度不低但讲得清楚"),
    ("BIO2001", "生命科学导论", "杨教授", 9.0, 3.5, 3.0, "给分好", "生物,通识", 28, "非常有趣的通识课"),
]
for i, r in enumerate(reviews):
    lines.append(
        f"INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) "
        f"VALUES ({i+1}, '{esc(r[0])}', '{esc(r[1])}', '{esc(r[2])}', {r[3]}, {r[4]}, {r[5]}, '{esc(r[6])}', '{esc(r[7])}', {r[8]}, '{esc(r[9])}');"
    )
lines.append('')

# teacher_reviews
lines.append('-- 教师评价')
teachers = [
    ("李教授", "数学分析B1,数学分析B2", 8.5, "讲课清晰、板书详细", "讲解透彻", "进度快", "讲课质量高，数学基础扎实", 120),
    ("王教授", "机器学习导论,深度学习", 8.8, "善于结合实例", "科研能力强", "实验要求高", "AI课程紧跟前沿", 85),
    ("张教授", "大学物理B,力学", 8.0, "讲课细致", "态度认真", "课程偏难", "物理课难度不低但讲得清楚", 70),
]
for i, t in enumerate(teachers):
    lines.append(
        f"INSERT OR REPLACE INTO teacher_reviews (id, name, courses, avg_rating, teaching_style, strengths, weaknesses, review_summary, review_count) "
        f"VALUES ({i+1}, '{esc(t[0])}', '{esc(t[1])}', {t[2]}, '{esc(t[3])}', '{esc(t[4])}', '{esc(t[5])}', '{esc(t[6])}', {t[7]});"
    )
lines.append('')

# events
lines.append('-- 日程事件')
events = [
    (STUDENT, "组会", "meeting", "2026-06-15 14:00", "2026-06-15 16:00", "科研楼301", "每周组会"),
    (STUDENT, "期中考试-数学分析", "exam", "2026-04-15 09:00", "2026-04-15 11:00", "三教3A101", ""),
    (STUDENT, "课程论文截止", "deadline", "2026-06-20 23:59", "2026-06-20 23:59", "", "操作系统课程论文"),
]
for i, e in enumerate(events):
    lines.append(
        f"INSERT OR REPLACE INTO events (id, student_id, title, event_type, start_time, end_time, location, description) "
        f"VALUES ({i+1}, '{STUDENT}', '{esc(e[1])}', '{esc(e[2])}', '{esc(e[3])}', '{esc(e[4])}', '{esc(e[5])}', '{esc(e[6])}');"
    )

lines.append('"""')

content = "\n".join(lines) + "\n"
with open(r"f:\小蜗\database\seed_data.py", "w", encoding="utf-8") as f:
    f.write(content)

print(f"\n✅ 已生成 seed_data.py ({len(lines)} 行)")
