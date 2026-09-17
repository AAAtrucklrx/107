from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.evaluate("""() => {
      const scroll = document.querySelector('.message-scroll');
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      const a = document.createElement('article');
      a.className = 'message message--assistant';
      a.innerHTML = '<div class="message__body"><div class="markdown-body"><p>测试角标 <sup class="cite">1</sup> 结束。</p></div></div>';
      scroll.appendChild(a);
    }""")
    page.wait_for_timeout(300)
    info = page.evaluate("""() => {
      const sup = document.querySelector('sup.cite');
      if (!sup) return { found: false };
      const cs = getComputedStyle(sup);
      const r = sup.getBoundingClientRect();
      return { found: true, display: cs.display, color: cs.color, bg: cs.backgroundColor, fontSize: cs.fontSize,
               w: r.width, h: r.height, text: sup.textContent,
               rules: (() => { let out = []; for (const sheet of document.styleSheets) {
                 try { for (const r2 of sheet.cssRules) {
                   if (r2.selectorText && r2.selectorText.includes('sup')) out.push(r2.selectorText + ' {' + r2.style.cssText + '}');
                   if (r2.cssRules) { for (const inner of r2.cssRules) { if (inner.selectorText && inner.selectorText.includes('sup')) out.push('[media] ' + inner.selectorText + ' {' + inner.style.cssText + '}'); } }
                 } } catch(e) {}
               } return out; })() };
    }""")
    for k, v in info.items():
        print(k, "=>", v)
    browser.close()
