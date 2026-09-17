from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766/campus", wait_until="networkidle")
    info = page.evaluate("""() => {
      const b = document.querySelector('.campus-search button');
      const cs = getComputedStyle(b);
      const rules = [];
      for (const sheet of document.styleSheets) {
        try {
          for (const r of sheet.cssRules) {
            if (r.selectorText && r.selectorText.includes('campus-search button') && r.style && r.style.borderRadius) {
              rules.push(r.selectorText + ' -> ' + r.style.borderRadius);
            }
          }
        } catch {}
      }
      return { radius: cs.borderRadius, rules };
    }""")
    print(info)
    browser.close()
