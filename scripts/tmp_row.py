from playwright.sync_api import sync_playwright
import json

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "program-search" in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/for-std/program-search", wait_until="networkidle")
    page.wait_for_timeout(1500)
    html = page.evaluate("""() => {
      const rows = [...document.querySelectorAll('a,button')].filter(a => (a.textContent || '').includes('计算机科学与技术专业培养方案'));
      return rows.slice(0, 3).map(a => ({ tag: a.tagName, cls: a.className.slice(0, 60), text: a.textContent.trim().slice(0, 50), href: a.getAttribute('href') }));
    }""")
    print(json.dumps(html, ensure_ascii=False, indent=1))
    browser.close()
