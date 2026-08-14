import json

data = json.load(open(r"f:\小蜗\scripts\data\crawl_real_data.json"))

# Check lesson structure
print("=== 2026春季课程样例 ===")
lessons = data.get("lessons_421", [])
for i, l in enumerate(lessons[:5]):
    print(f"\n--- Lesson {i+1} ---")
    print(json.dumps(l, ensure_ascii=False, indent=2)[:600])

print("\n\n=== 专业课考试样例 ===")
exams = data.get("exams_421", [])
for i, e in enumerate(exams[:3]):
    print(f"\n--- Exam {i+1} ---")
    print(json.dumps(e, ensure_ascii=False, indent=2)[:500])

print("\n\n=== 通修课考试样例 ===")
gen = data.get("gen_exams_421", [])
for i, e in enumerate(gen[:3]):
    print(f"\n--- GenExam {i+1} ---")
    print(json.dumps(e, ensure_ascii=False, indent=2)[:500])

print("\n\n=== 教室占用样例 ===")
tt = data.get("timetable_2026-06-15", {})
for key in tt:
    val = tt[key]
    if isinstance(val, list):
        print(f"\n{key}: {len(val)} items")
        if val:
            print(f"  First: {json.dumps(val[0], ensure_ascii=False)[:300]}")
