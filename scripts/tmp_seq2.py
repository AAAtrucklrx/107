from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    pid = 2610  # 2023 英才班，从未访问

    def get(path):
        return page.evaluate("""async (p) => {
          const r = await fetch(p, { credentials: 'include' });
          const t = await r.text();
          return { s: r.status, n: t.length, eng: t.includes('英语通修') };
        }""", path)

    print("0) 初始 search 树:", get(f"/for-std/program-search/root-module-json/{pid}"))
    print("1) 激活 program 端点:", get(f"/for-std/program/root-module-json/{pid}"))
    for wait in (1, 3, 5):
        time.sleep(wait)
        r = get(f"/for-std/program-search/root-module-json/{pid}")
        print(f"2) 激活后+{wait}s search 树:", r)
        if r["eng"]:
            break
