"""用修复后的 3011 完整方案数据（含英语通修）更新 demo fixture。"""
from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict

FIXTURE = r"f:\小蜗\fixtures\demo\PB25111691.json"
PID = 3011

con = sqlite3.connect(r"f:\小蜗\data\course_data.db")
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT code, name, required, exam, credit, category, term FROM program_courses WHERE program_id=?",
    (PID,),
).fetchall()
con.close()
print(f"course_data.db 中 3011: {len(rows)} 条")

data = json.load(open(FIXTURE, encoding="utf-8"))
prog = data["program"]

prog["courses"] = [
    {
        "code": r["code"],
        "name": r["name"],
        "required": r["required"],
        "credit": r["credit"],
        "term": r["term"],
        "category": r["category"],
    }
    for r in rows
]

# modules 统计：按顶层模块（category 首段）聚合，required_credits=组内必修学分合计
mods: "OrderedDict[str, dict]" = OrderedDict()
for c in prog["courses"]:
    top = (c["category"] or "其他").split("/")[0]
    m = mods.setdefault(top, {"category": top, "required_credits": 0.0, "course_count": 0})
    m["course_count"] += 1
    try:
        if c["required"] == "必修":
            m["required_credits"] += float(c["credit"] or 0)
    except (TypeError, ValueError):
        pass
prog["modules"] = [dict(m) for m in mods.values()]
prog["totalCredits"] = 167  # 教务 requireInfo.requiredCredits（权威）

json.dump(data, open(FIXTURE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("modules:")
for m in prog["modules"]:
    print(f"  {m['category']}: {m['course_count']} 门, 必修学分 {m['required_credits']}")
eng = [c for c in prog["courses"] if "英语" in c["category"] or "英语" in c["name"]]
print(f"英语相关课程: {len(eng)} 门")
for c in eng[:5]:
    print("  ", c["code"], c["name"], c["required"])
