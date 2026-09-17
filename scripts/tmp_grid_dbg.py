from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=True, executable_path=exe)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    info = page.evaluate("""() => {
      const g = document.querySelector('.starter-prompt-grid');
      const empty = document.querySelector('.chat-empty');
      const scroll = document.querySelector('.message-scroll');
      const cs = getComputedStyle(g);
      return { cols: cs.gridTemplateColumns, gWidth: g.getBoundingClientRect().width,
               emptyW: empty?.getBoundingClientRect().width, emptyPad: getComputedStyle(empty).padding,
               scrollW: scroll.getBoundingClientRect().width, scrollPad: getComputedStyle(scroll).padding,
               mediaMatch: matchMedia('(max-width: 760px)').matches };
    }""")
    for k, v in info.items(): print(k, "=>", v)
    browser.close()
