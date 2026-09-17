from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8767/draft_c_dual.html", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.screenshot(path="docs/qa/tmp-draft-home-1440.png")
    browser.close()
    print("saved")
