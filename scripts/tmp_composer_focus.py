from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.click(".composer textarea")
    page.wait_for_timeout(300)
    page.locator(".composer").screenshot(path="docs/qa/tmp-composer-focus.png")
    print("saved (focused state)")
    browser.close()
