from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    info = page.evaluate("""() => {
      const b = document.querySelector('.brand');
      const cs = getComputedStyle(b);
      return { display: cs.display, dir: cs.flexDirection, wrap: cs.flexWrap, align: cs.alignItems,
               w: b.getBoundingClientRect().width, cls: b.className,
               rules: (() => { let out = []; for (const sheet of document.styleSheets) {
                 try { for (const r of sheet.cssRules) {
                   if (r.selectorText && r.selectorText.includes('.brand') && !r.selectorText.includes('__')) out.push(r.selectorText + ' {' + r.style.cssText + '}');
                 } } catch(e) {}
               } return out; })() };
    }""")
    for k, v in info.items():
        print(k, "=>", v)
    browser.close()
