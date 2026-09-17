import json
data = json.loads(open(r"f:\小蜗\scripts\data\programs_jw\3011.json", encoding="utf-8").read())
cats = sorted({c["category"] for c in data["courses"] if "英语" in c["category"] or "外语" in c["category"]})
print("3011 英语相关分组:", cats)
n = sum(1 for c in data["courses"] if "英语" in c["name"])
print("3011 英语课名数量:", n)
