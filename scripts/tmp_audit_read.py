import json
data = json.loads(open(r"f:\小蜗\scripts\data\jw_api_audit.json", encoding="utf-8").read())

# 个人培养方案树的结构
entry = data.get("https://jw.ustc.edu.cn/for-std/program/root-module-json/3011")
if entry:
    s = json.dumps(entry["samples"][0]["structure"], ensure_ascii=False)
    print("个人方案树结构片段:")
    print(s[:600])
    print()
    print("含'英语':", "英语" in json.dumps(entry, ensure_ascii=False))
else:
    print("audit 里无该端点样本")

# 各数据接口结构清单
for url, e in data.items():
    short = url.replace("https://jw.ustc.edu.cn", "")
    st = json.dumps(e["samples"][0].get("structure", {}), ensure_ascii=False)[:150]
    print(f"\n[{e['method']}] {short}")
    print("   ", st)
