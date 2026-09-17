import json
data = json.load(open(r"f:\小蜗\fixtures\demo\PB25111691.json", encoding="utf-8"))
prog = data["program"]
print("program 键:", list(prog.keys()))
print(json.dumps(prog, ensure_ascii=False)[:1200])
