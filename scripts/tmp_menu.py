from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if pg.url.rstrip("/").endswith("/home") or "program-search" not in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/home", wait_until="networkidle")
    page.wait_for_timeout(1500)
    links = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('a').forEach(a => {
        const t = (a.textContent || '').trim();
        const h = a.getAttribute('href') || '';
        if (t && (t.includes('计划') || t.includes('培养') || t.includes('执行') || t.includes('课表') || t.includes('成绩') || t.includes('考试'))) {
          out.push(t + ' -> ' + h);
        }
      });
      return [...new Set(out)];
    }""")
    for l in links:
        print(" ", l)
