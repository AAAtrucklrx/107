"""
爬取 catalog.ustc.edu.cn 公开 API 数据，生成测试样例
"""
import json
import requests
from datetime import datetime
from pathlib import Path

BASE = "https://catalog.ustc.edu.cn"
# 爬取产物统一写入 scripts/data/（gitignored，不入库）
DATA_DIR = Path(__file__).resolve().parent / "data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

def fetch(url, label=""):
    print(f"\n{'='*60}")
    print(f"[{label}] GET {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        print(f"  Status: {resp.status_code}")
        data = resp.json()
        if isinstance(data, dict):
            print(f"  Keys: {list(data.keys())[:10]}")
        elif isinstance(data, list):
            print(f"  List length: {len(data)}")
            if data:
                print(f"  First item keys: {list(data[0].keys())[:10] if isinstance(data[0], dict) else type(data[0])}")
        return data
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

# 1. 学期列表
print("\n" + "="*60)
print("1. 学期列表")
semesters = fetch(f"{BASE}/api/teach/semester/list", "学期列表")
if semesters:
    with open(DATA_DIR / "crawl_semesters.json", "w", encoding="utf-8") as f:
        json.dump(semesters, f, ensure_ascii=False, indent=2)
    print(f"  已保存到 scripts/data/crawl_semesters.json")

# 2. 今天的空教室
today = datetime.now().strftime("%Y-%m-%d")
print(f"\n{'='*60}")
print(f"2. 今日空教室 ({today})")
timetable = fetch(f"{BASE}/api/teach/timetable-public-all/{today}", "空教室")
if timetable:
    with open(DATA_DIR / "crawl_timetable.json", "w", encoding="utf-8") as f:
        json.dump(timetable, f, ensure_ascii=False, indent=2)
    print(f"  已保存到 scripts/data/crawl_timetable.json")
    # 统计
    if isinstance(timetable, list):
        print(f"  总教室记录数: {len(timetable)}")
    elif isinstance(timetable, dict):
        for k, v in timetable.items():
            if isinstance(v, list):
                print(f"  {k}: {len(v)} 条记录")

# 3. 专业课考试（需要学期 ID）
sem_id = None
if semesters:
    if isinstance(semesters, list) and semesters:
        sem_id = semesters[0].get("id") or semesters[0].get("semesterId")
    elif isinstance(semesters, dict):
        items = semesters.get("data", semesters.get("result", []))
        if items and isinstance(items, list):
            sem_id = items[0].get("id") or items[0].get("semesterId")

if sem_id:
    print(f"\n{'='*60}")
    print(f"3. 专业课考试 (semId={sem_id})")
    exams = fetch(f"{BASE}/api/teach/exam/list/{sem_id}", "专业课考试")
    if exams:
        with open(DATA_DIR / "crawl_exams.json", "w", encoding="utf-8") as f:
            json.dump(exams, f, ensure_ascii=False, indent=2)
        print(f"  已保存到 scripts/data/crawl_exams.json")

    print(f"\n{'='*60}")
    print(f"4. 通修课考试 (semId={sem_id})")
    gen_exams = fetch(f"{BASE}/api/teach/general-exam/list/{sem_id}", "通修课考试")
    if gen_exams:
        with open(DATA_DIR / "crawl_general_exams.json", "w", encoding="utf-8") as f:
            json.dump(gen_exams, f, ensure_ascii=False, indent=2)
        print(f"  已保存到 scripts/data/crawl_general_exams.json")

    print(f"\n{'='*60}")
    print(f"5. 课程列表 (semId={sem_id})")
    lessons = fetch(f"{BASE}/api/teach/lesson/list-for-teach/{sem_id}", "课程列表")
    if lessons:
        with open(DATA_DIR / "crawl_lessons.json", "w", encoding="utf-8") as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        print(f"  已保存到 scripts/data/crawl_lessons.json (可能较大)")
else:
    print(f"\n  ⚠️ 无法获取学期 ID，跳过考试和课程爬取")

# 4. 课程搜索样例
print(f"\n{'='*60}")
print("6. 课程搜索样例")
for keyword in ["数学", "物理", "计算机"]:
    results = fetch(f"{BASE}/api/teach/course/search?keyword={keyword}", f"搜索:{keyword}")
    if results:
        with open(DATA_DIR / f"crawl_search_{keyword}.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n{'='*60}")
print("爬取完成！")
