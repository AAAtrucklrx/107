import json
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    body = page.evaluate("""async () => {
      const r = await fetch('/for-std/program-search/root-module-json/2763', { credentials: 'include' });
      return await r.json();
    }""")
    Path(r"f:\小蜗\scripts\data\tree_2763_search.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print("saved", len(json.dumps(body, ensure_ascii=False)))

def analyze(node, depth=0, stats=None):
    if stats is None:
        stats = {"nodes": 0, "pc": 0, "maxdepth": 0}
    stats["nodes"] += 1
    stats["pc"] += len(node.get("planCourses") or [])
    stats["maxdepth"] = max(stats["maxdepth"], depth)
    for ch in node.get("children") or []:
        analyze(ch, depth + 1, stats)
    return stats

stats = analyze(body)
print("节点数:", stats["nodes"], "| planCourses 总数:", stats["pc"], "| 最大深度:", stats["maxdepth"])
print("顶层键:", list(body.keys()))
print("root type:", (body.get("type") or {}).get("nameZh"))
for c in (body.get("children") or [])[:10]:
    t = (c.get("type") or {}).get("nameZh")
    print(f"  type={t} pc={len(c.get('planCourses') or [])} children={len(c.get('children') or [])}")
