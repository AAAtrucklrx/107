import json
menu = json.load(open(r"f:\小蜗\scripts\data\jw_menu.json", encoding="utf-8"))
items = [(m.get("id"), m.get("title"), m.get("href")) for m in menu]
for it in items:
    if any(k in (it[1] or "") for k in ("计划", "培养", "课表", "成绩")):
        print(it)
