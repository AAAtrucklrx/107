import json
from pathlib import Path
files = list(Path(r"f:\小蜗\scripts\data\programs_jw").glob("*.json"))
if len(files) >= 1202:
    for pid in ("3011", "2763", "2612", "3044"):
        f = Path(rf"f:\小蜗\scripts\data\programs_jw\{pid}.json")
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            eng = sum(1 for c in d.get("courses", []) if "英语" in c.get("name", "") or "英语" in c.get("category", ""))
            print(f"{pid}: courses={len(d.get('courses', []))}, 英语相关={eng}")
