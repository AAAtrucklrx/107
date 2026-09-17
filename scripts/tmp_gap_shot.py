from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 424, "height": 956}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8766/campus", wait_until="networkidle")
    page.wait_for_timeout(400)
    gap = page.evaluate("""() => {
      const header = document.querySelector('header.mobile-header');
      const title = [...document.querySelectorAll('h1, h2')].find(h => h.textContent.includes('校园服务'));
      return title && header ? Math.round(title.getBoundingClientRect().top - header.getBoundingClientRect().bottom) : null;
    }""")
    print("标题与顶栏间距(px):", gap)
    page.screenshot(path="docs/qa/mobile-gap-fixed.png")
    browser.close()
