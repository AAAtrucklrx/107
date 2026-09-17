import json
data = json.load(open(r"f:\小蜗\fixtures\demo\PB25111691.json", encoding="utf-8"))
print("顶层键:", list(data.keys()))
s = json.dumps(data, ensure_ascii=False)
print("含'英语':", "英语" in s, "| 含'外语':", "外语" in s, "| 含'通修':", "通修" in s)
# 找培养方案树
for key in ("program_tree", "program", "personal_program", "training_program"):
    if key in data:
        print(f"发现键: {key}")
tree = data.get("program_tree") or data.get("personal_program")
if tree:
    def walk(n, d=0):
        t = n.get("type") or {}
        name = t.get("nameZh") if isinstance(t, dict) else (n.get("name") or n.get("category") or "")
        n_pc = len(n.get("planCourses") or [])
        courses = n.get("courses")
        print("  " * d + f"{name} planCourses={n_pc} courses={len(courses) if courses else 0} children={len(n.get('children') or [])}")
        for ch in n.get("children") or []:
            walk(ch, d + 1)
    if isinstance(tree, list):
        for t in tree:
            walk(t)
    else:
        walk(tree)
else:
    # 列出所有含 courses 的键
    for k, v in data.items():
        if isinstance(v, (list, dict)):
            print(f"  {k}: {type(v).__name__} len={len(v)}")
