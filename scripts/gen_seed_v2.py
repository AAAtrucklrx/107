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

# 生成 SQL
def esc(s): return str(s).replace("'", "''").replace('\n', ' ').replace('\r', '').strip()

lines = []
lines.append('"""')
lines.append('小蜗 — 种子数据模块')
lines.append('基于真实 catalog API 数据生成 (2026 春季学期)')
lines.append('"""')
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

lines.append('"""')

content = "\n".join(lines) + "\n"
with open(r"f:\小蜗\database\seed_data.py", "w", encoding="utf-8") as f:
    f.write(content)

print(f"\n✅ 已生成 seed_data.py ({len(lines)} 行)")
