import re
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8765", wait_until="networkidle")
    page.locator("aside.desktop-rail").get_by_role("button", name=re.compile("未登录")).click()
    page.get_by_role("menuitem", name="进入演示身份").click()
    page.locator(".today-strip").wait_for(timeout=15000)
    page.wait_for_timeout(400)
    page.screenshot(path="docs/qa/glass-today-strip-desktop.png")
    page.set_viewport_size({"width": 424, "height": 956})
    page.wait_for_timeout(400)
    page.screenshot(path="docs/qa/glass-today-strip-mobile.png")
    print("saved both")
    browser.close()
