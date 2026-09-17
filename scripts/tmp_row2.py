from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "program-search" in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/for-std/program-search", wait_until="networkidle")
    page.wait_for_timeout(1500)
    info = page.evaluate("""() => {
      const tds = [...document.querySelectorAll('td')].filter(td => (td.textContent || '').includes('计算机科学与技术专业培养方案'));
      if (!tds.length) return { found: false, bodySnippet: document.body.innerText.slice(0, 500) };
      const row = tds[0].closest('tr');
      const btns = [...row.querySelectorAll('a,button')].map(b => ({ text: b.textContent.trim(), href: b.getAttribute('href'), onclick: (b.getAttribute('onclick') || '').slice(0, 60) }));
      return { found: true, btns };
    }""")
    import json as j
    print(j.dumps(info, ensure_ascii=False, indent=1))
    browser.close()
