from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8766/campus", wait_until="networkidle")
    page.locator(".compact-select select").focus()
    page.wait_for_timeout(300)
    page.locator(".compact-select").screenshot(path="docs/qa/compact-select-focus.png")
    browser.close()
    print("saved (focused)")
