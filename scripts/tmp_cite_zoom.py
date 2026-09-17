from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=3)
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.evaluate("""() => {
      const scroll = document.querySelector('.message-scroll');
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      const a = document.createElement('article');
      a.className = 'message message--assistant';
      a.innerHTML = '<div class="message__body"><div class="markdown-body"><p>根据评课社区近三年数据 <sup class="cite">1</sup> 和你本学期的课表负载 <sup class="cite">2</sup>，给出对比：</p></div></div>';
      scroll.appendChild(a);
    }""")
    page.wait_for_timeout(300)
    sup = page.evaluate("""() => { const s = document.querySelector('sup.cite'); const cs = getComputedStyle(s); return { bg: cs.backgroundColor, color: cs.color }; }""")
    print(sup)
    page.locator(".message--assistant").screenshot(path="docs/qa/tmp-cite-zoom.png")
    browser.close()
