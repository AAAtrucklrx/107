import json
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    body = page.evaluate("""async () => {
      const r = await fetch('/for-std/program/root-module-json/3011', { credentials: 'include' });
      return await r.json();
    }""")
    Path(r"f:\小蜗\scripts\data\tree_3011_full.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print("树已保存:", len(json.dumps(body, ensure_ascii=False)), "bytes")
    ch = body.get("children") or []
    print("root.children 数:", len(ch), "| root.planCourses:", len(body.get("planCourses") or []))
    for c in ch[:12]:
        t = (c.get("type") or {}).get("nameZh")
        print(f"  type={t} planCourses={len(c.get('planCourses') or [])} children={len(c.get('children') or [])}")
