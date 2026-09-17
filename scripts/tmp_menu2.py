import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/home", wait_until="networkidle")
    page.wait_for_timeout(1500)
    menu = page.evaluate("""async () => {
      const r = await fetch('/home/menu', { credentials: 'include' });
      return await r.json();
    }""")
    out = []
    def walk(nodes, prefix=""):
        for n in nodes:
            name = n.get("nameZh") or n.get("name") or ""
            path = n.get("path") or n.get("url") or n.get("href") or ""
            if name:
                out.append(f"{prefix}{name} -> {path}")
            for key in ("children", "subMenus", "menus", "items"):
                if isinstance(n.get(key), list):
                    walk(n[key], prefix + name + " / ")
    if isinstance(menu, list):
        walk(menu)
    elif isinstance(menu, dict):
        for k, v in menu.items():
            if isinstance(v, list):
                walk(v)
    hits = [o for o in out if "执行" in o or "计划" in o or "培养" in o]
    print("全部菜单项:", len(out))
    for o in hits:
        print("  ", o)
    json.dump(menu, open(r"f:\小蜗\scripts\data\jw_menu.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
