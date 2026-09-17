from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto("http://127.0.0.1:8766/campus", wait_until="networkidle")
    page.get_by_role("tab", name="活动").click()
    page.wait_for_timeout(1500)
    page.screenshot(path="docs/qa/activity-cards-clean.png")
    # 点击第一个活动卡 → 详情弹窗
    page.locator(".catalog-rows--tiles .activity-item").first.click()
    page.wait_for_timeout(500)
    assert page.locator(".activity-detail").is_visible()
    page.screenshot(path="docs/qa/activity-detail-dialog.png")
    browser.close()
    print("card + detail dialog: PASS")
