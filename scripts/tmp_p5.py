import json
from pathlib import Path
import sqlite3

# 找 3011 的原始 JSON 文件名
con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
row = con.execute("SELECT name, grade FROM programs WHERE id=3011").fetchone()
print("方案:", row)

# 逐个试：文件名可能含 id
files = list(Path(r"f:\小蜗\scripts\data\programs_jw").glob("*3011*"))
print("匹配文件:", [f.name for f in files])

if files:
    data = json.loads(files[0].read_text(encoding="utf-8"))
    def walk(node, depth=0, cats=None):
        t = node.get("type")
        name = t.get("nameZh") if isinstance(t, dict) else None
        n = len(node.get("planCourses") or [])
        if depth <= 2:
            print("  " * depth + f"{name} (planCourses={n}, children={len(node.get('children') or [])})")
        for ch in node.get("children") or []:
            walk(ch, depth + 1)
    root = data if isinstance(data, dict) else {}
    trees = root.get("modules") or root.get("data") or root
    print("顶层键:", list(root.keys())[:10] if isinstance(root, dict) else type(root))
    if isinstance(trees, list):
        for t in trees:
            walk(t)
    elif isinstance(trees, dict):
        for k, v in trees.items():
            print(k, type(v))
