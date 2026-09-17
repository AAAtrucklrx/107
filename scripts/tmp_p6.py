import json
data = json.loads(open(r"f:\小蜗\scripts\data\programs_jw\3011.json", encoding="utf-8").read())

def walk(node, depth=0):
    t = node.get("type")
    name = t.get("nameZh") if isinstance(t, dict) else None
    n = len(node.get("planCourses") or [])
    if depth <= 2:
        print("  " * depth + f"{name} (planCourses={n}, children={len(node.get('children') or [])})")
    for ch in node.get("children") or []:
        walk(ch, depth + 1)

courses = data["courses"]
print("courses 顶层节点数:", len(courses))
for t in courses:
    walk(t)

# 全树搜外语/英语节点
hits = []
def search(node, path=""):
    t = node.get("type")
    name = t.get("nameZh") if isinstance(t, dict) else None
    full = f"{path}/{name}" if name else path
    if name and ("外语" in name or "英语" in name):
        hits.append(full)
    for ch in node.get("children") or []:
        search(ch, full)
for t in courses:
    search(t)
print("\n树中含外语/英语的节点:", hits or "无")
