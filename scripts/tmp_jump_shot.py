from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.evaluate("""() => {
      const scroll = document.querySelector('.message-scroll');
      const empty = scroll?.querySelector('.chat-empty');
      if (empty) empty.remove();
      for (let i = 0; i < 40; i += 1) {
        const a = document.createElement('article');
        a.className = 'message message--assistant';
        a.innerHTML = `<div class=\"message__body\"><div class=\"markdown-body\"><p>示例回答 ${i}，用于撑起滚动高度，内容略长一点以便产生滚动距离。</p></div></div>`;
        scroll.appendChild(a);
      }
      scroll.scrollTop = 0;
      scroll.dispatchEvent(new Event('scroll', { bubbles: true }));
    }""")
    page.wait_for_timeout(500)
    btn = page.locator(".jump-to-latest")
    print("button visible:", btn.count() == 1 and btn.is_visible())
    page.screenshot(path="docs/qa/jump-to-latest-pill.png", clip={"x": 900, "y": 680, "width": 540, "height": 320})
    browser.close()
