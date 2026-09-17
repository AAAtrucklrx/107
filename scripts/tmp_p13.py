import json, random
from pathlib import Path
files = list(Path(r"f:\小蜗\scripts\data\programs_jw").glob("*.json"))
random.seed(1)
sample = random.sample(files, 15)
for f in sample:
    d = json.loads(f.read_text(encoding="utf-8"))
    print(f"{f.stem:>6} {d.get('grade',''):>6} courses={len(d.get('courses', [])):>4} bytes={f.stat().st_size:>9}")
small = sum(1 for f in files if len(json.loads(f.read_text(encoding='utf-8')).get('courses', [])) < 20)
print(f"courses<20 的文件: {small}/{len(files)}")
