import json, requests

BASE = "https://catalog.ustc.edu.cn"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def fetch(url, label=""):
    print(f"[{label}] {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    data = resp.json()
    if isinstance(data, dict) and "timetable" in data:
        data = data["timetable"]
    print(f"  -> {len(data) if isinstance(data, list) else type(data)} items")
    return data

# 用正确的学期 ID
sem_ids = [
    (421, "2026春季"),
    (461, "2026秋季"),
    (441, "2026夏季"),
]

all_data = {}

for sem_id, sem_name in sem_ids:
    print(f"\n=== {sem_name} (id={sem_id}) ===")
    
    # 专业课考试
    exams = fetch(f"{BASE}/api/teach/exam/list/{sem_id}", "专业课考试")
    if exams:
        all_data[f"exams_{sem_id}"] = exams
    
    # 通修课考试
    gen = fetch(f"{BASE}/api/teach/general-exam/list/{sem_id}", "通修课考试")
    if gen:
        all_data[f"gen_exams_{sem_id}"] = gen
    
    # 课程列表 (只取前50条做样例)
    lessons = fetch(f"{BASE}/api/teach/lesson/list-for-teach/{sem_id}", "课程列表")
    if lessons:
        all_data[f"lessons_{sem_id}"] = lessons[:50] if isinstance(lessons, list) and len(lessons) > 50 else lessons

# 空教室：用一个有课的工作日
for date in ["2026-06-15", "2026-06-16"]:
    tt = fetch(f"{BASE}/api/teach/timetable-public-all/{date}", f"教室({date})")
    if tt:
        all_data[f"timetable_{date}"] = tt

# 保存
with open(r"f:\小蜗\scripts\crawl_real_data.json", "w", encoding="utf-8") as f:
    json.dump(all_data, f, ensure_ascii=False, indent=2)

# 打印摘要
print("\n\n=== 数据摘要 ===")
for key, val in all_data.items():
    if isinstance(val, list):
        print(f"  {key}: {len(val)} 条")
        if val and isinstance(val[0], dict):
            print(f"    字段: {list(val[0].keys())[:8]}")
    elif isinstance(val, dict):
        print(f"  {key}: dict with keys {list(val.keys())[:5]}")

print("\n完成！保存到 crawl_real_data.json")
