import json, sys
sys.path.insert(0, r"f:\小蜗\scripts")
data = json.loads(open(r"f:\小蜗\scripts\data\programs_jw\3011.json", encoding="utf-8").read())

# 用爬虫的 extract_courses 调试
from crawl_jw_programs import extract_courses
# 3011.json 的 courses 是扁平结果; 需要原始树——没有保存!
print("新 3011.json 顶层键:", list(data.keys()))
print("courses 数量:", len(data["courses"]))
cats = sorted({c["category"] for c in data["courses"]})
print("category 数:", len(cats))
print("样本:", cats[:8])
