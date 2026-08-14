import json

data = json.load(open(r"f:\小蜗\scripts\data\crawl_semesters.json"))
print(f"Total semesters: {len(data)}")
print("\n--- First 5 (head) ---")
for s in data[:5]:
    print(f"  id={s['id']}, name={s['nameZh']}, code={s['code']}")
print("\n--- Last 5 (tail) ---")
for s in data[-5:]:
    print(f"  id={s['id']}, name={s['nameZh']}, code={s['code']}")

# Find current semester (2025-2026)
print("\n--- 2025-2026 semesters ---")
for s in data:
    if "2025" in s.get("nameZh", "") or "2026" in s.get("nameZh", ""):
        print(f"  id={s['id']}, name={s['nameZh']}, code={s['code']}, start={s.get('start')}, end={s.get('end')}")

# Check timetable structure
tt = json.load(open(r"f:\小蜗\scripts\data\crawl_timetable.json"))
if "timetable" in tt:
    items = tt["timetable"]
    print(f"\nTimetable records: {len(items)}")
    if items:
        print(f"First record keys: {list(items[0].keys())}")
        print(f"Sample: {json.dumps(items[0], ensure_ascii=False)[:300]}")
