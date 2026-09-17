import json
data = json.loads(open(r"f:\小蜗\scripts\data\jw_api_full.json", encoding="utf-8").read())
info = data["/for-std/program/root-module-json/3011"]
print("英语节点路径:")
for p in (info.get("英语节点路径") or [])[:12]:
    print("  ", p)
