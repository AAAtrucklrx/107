import json
body = json.loads(open(r"f:\小蜗\scripts\data\tree_2763_search.json", encoding="utf-8").read())
all_pc = body.get("allPlanCourses") or []
print("allPlanCourses 数量:", len(all_pc))
print("首个元素键:", list(all_pc[0].keys()) if all_pc else "空")
eng = [pc for pc in all_pc if "英语" in ((pc.get("course") or {}).get("nameZh") or "")]
print("含英语的课:", len(eng))
if eng:
    print("样本:", json.dumps(eng[0], ensure_ascii=False)[:300])
# 有没有模块归属信息
for pc in all_pc[:2]:
    print("planCourse keys:", list(pc.keys()))
