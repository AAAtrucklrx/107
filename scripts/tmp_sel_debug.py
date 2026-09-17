from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766/campus", wait_until="networkidle")
    sel = page.locator(".compact-select select")
    sel.focus()
    page.wait_for_timeout(200)
    info = page.evaluate("""() => {
      const s = document.querySelector('.compact-select select');
      const cs = getComputedStyle(s);
      const hits = [];
      const walk = (rules, media) => {
        for (const r of rules) {
          if (r.cssRules) { walk(r.cssRules, r.conditionText || media); continue; }
          if (!r.selectorText || !r.style) continue;
          const sel = r.selectorText;
          if ((sel.includes('select') || sel.includes('focus')) && (r.style.borderColor || r.style.outline || r.style.border)) {
            try { if (s.matches(sel)) hits.push((media ? '@media ' + media + ' ' : '') + sel + ' -> border:' + (r.style.border || '') + ' borderColor:' + (r.style.borderColor || '') + ' outline:' + (r.style.outline || '')); } catch {}
          }
        }
      };
      for (const sheet of document.styleSheets) { try { walk(sheet.cssRules, null); } catch {} }
      return { border: cs.border, outline: cs.outline, boxShadow: cs.boxShadow.slice(0, 60), hits };
    }""")
    for k, v in info.items():
        print(k, "=", v)
    browser.close()
