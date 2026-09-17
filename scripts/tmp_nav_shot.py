from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 424, "height": 956}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.wait_for_timeout(400)
    page.screenshot(path="docs/qa/bottom-nav-harmony.png", clip={"x": 0, "y": 806, "width": 424, "height": 150})
    browser.close()
    print("saved")
