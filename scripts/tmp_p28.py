import json
data = json.loads(open(r"f:\小蜗\scripts\data\jw_api_all_pages.json", encoding="utf-8").read())
XIAOWO_USED = ("/for-std/student-info/info", "/for-std/course-table/get-data", "/for-std/course-table/semester",
    "/for-std/course-take-query/semester", "/for-std/grade/sheet/getSemesters", "/for-std/grade/sheet/getGradeList",
    "/for-std/program/root-module-json", "/for-std/sport-grade/list", "/for-std/exam-arrange/info",
    "/home/get-current-teach-week", "/home/menu")
used, unused = [], []
for url, info in data.items():
    short = url.replace("https://jw.ustc.edu.cn", "").split("?")[0]
    row = {"url": short, "bytes": info["bytes"], "pages": info.get("pages", ["?"]), "struct": info.get("structure", "")[:110]}
    (used if any(short.startswith(u) or u in short for u in XIAOWO_USED) else unused).append(row)
print(f"=== 已用 {len(used)} ===")
for r in used:
    print(f"  {r['url']}  [{r['pages'][0][:22] if r['pages'] else ''}]")
print(f"\n=== 未用 {len(unused)} ===")
for r in sorted(unused, key=lambda x: -x["bytes"]):
    print(f"  [{r['bytes']:>8}B] {r['url']}")
    print(f"      页面: {r['pages'][0][:40] if r['pages'] else '?'}")
    print(f"      结构: {r['struct'][:100]}")
