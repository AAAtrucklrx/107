from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    for pid in ("3011", "2763"):
        r = page.evaluate("""async (pid) => {
          const r = await fetch('/for-std/program-search/root-module-json/' + pid, { credentials: 'include' });
          const t = await r.text();
          return { size: t.length, FL1007: t.includes('FL1007'), 英语通修L3: t.includes('英语通修L3'), 英语交流进阶I: t.includes('英语交流进阶I'), 大学英语: t.includes('大学英语') };
        }""", pid)
        print(f"pid {pid}:", r)
