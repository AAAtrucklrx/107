from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    for width, height, tag in ((1440, 1000, "desktop"), (424, 956, "mobile")):
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
        page.goto("http://127.0.0.1:8766/campus", wait_until="networkidle")
        page.get_by_role("tab", name="活动").click()
        page.wait_for_timeout(1500)
        page.screenshot(path=f"docs/qa/activity-tiles-{tag}-light.png")
        if tag == "mobile":
            page.locator("header.mobile-header button.account-trigger, header.mobile-header [aria-label*=未登录]").first.click()
            page.get_by_role("menuitem", name="深色主题").click()
        else:
            page.locator("aside.desktop-rail button.account-trigger").click()
            page.get_by_role("menuitem", name="深色主题").click()
        page.wait_for_timeout(500)
        page.screenshot(path=f"docs/qa/activity-tiles-{tag}-dark.png")
        page.close()
    browser.close()
    print("activity tile shots saved")
