import json
data = json.loads(open(r"f:\小蜗\scripts\data\jw_api_audit.json", encoding="utf-8").read())
print("== 教务系统捕获的端点（audit 阶段） ==")
for url, e in data.items():
    print(f"  [{e['method']}] {url.replace('https://jw.ustc.edu.cn','')}  samples={len(e['samples'])}")
full = json.loads(open(r"f:\小蜗\scripts\data\jw_api_full.json", encoding="utf-8").read())
print("\n== 关键接口响应形态 ==")
for url, info in full.items():
    print(f"  {url.replace('https://jw.ustc.edu.cn','')}  {info['bytes']}B  {info['label']}")
