import json, sys
sys.path.insert(0, r"f:\小蜗\scripts")
from crawl_jw_programs import extract_courses

tree = json.loads(open(r"f:\小蜗\scripts\data\tree_3011_full.json", encoding="utf-8").read())
out = extract_courses(tree, [])
print("extract 总数:", len(out))
cats = sorted({c["category"] for c in out})
print("category 数:", len(cats))
for c in cats:
    print("  ", c)
eng = [c for c in out if "英语" in c["name"] or "英语" in c["category"]]
print("英语相关:", len(eng), eng[:3])
