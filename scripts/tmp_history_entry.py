from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    # 桌面收起态：顶条历史按钮 → 抽屉打开
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.evaluate("() => localStorage.setItem('xiaowo-rail', 'collapsed')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(300)
    page.locator(".rail-topbar button[aria-label='会话历史']").click()
    page.wait_for_timeout(400)
    assert page.locator(".chat-history, [role='dialog']").first.is_visible()
    print("collapsed topbar history: PASS")
    page.screenshot(path="docs/qa/history-entry-collapsed.png")

    # 移动端：顶栏历史按钮 → 抽屉打开
    mobile = browser.new_page(viewport={"width": 424, "height": 956})
    mobile.goto("http://127.0.0.1:8766", wait_until="networkidle")
    mobile.wait_for_timeout(300)
    mobile.locator("header.mobile-header button[aria-label='会话历史']").click()
    mobile.wait_for_timeout(400)
    assert mobile.locator(".chat-history, [role='dialog']").first.is_visible()
    print("mobile header history: PASS")
    mobile.screenshot(path="docs/qa/history-entry-mobile.png")
    browser.close()
