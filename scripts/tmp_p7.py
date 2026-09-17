import json
data = json.loads(open(r"f:\小蜗\scripts\data\programs_jw\3011.json", encoding="utf-8").read())
courses = data["courses"]
print("第一个节点键:", list(courses[0].keys()))
print(json.dumps(courses[0], ensure_ascii=False)[:400])
print()
# 有 type 的节点比例
typed = [c for c in courses if c.get("type")]
print(f"有 type 的顶层节点: {len(typed)}/{len(courses)}")
if typed:
    print("第一个有 type 的:", json.dumps(typed[0], ensure_ascii=False)[:300])
# 全部节点的 name 字段分布（可能是 nameZh 之外的字段）
names = set()
def collect(n):
    for k in ("nameZh", "name", "title"):
        if n.get(k):
            names.add(str(n[k])[:20])
    for ch in n.get("children") or []:
        collect(ch)
for t in courses:
    collect(t)
print("树中出现的名称样本:", sorted(names)[:30])
