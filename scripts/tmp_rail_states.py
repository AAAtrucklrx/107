"""侧栏三态截图：常驻展开 / 收起顶条 / 抽屉浮层 + 移动端对照。"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

def rail_offscreen(page) -> bool:
    box = page.locator("aside.desktop-rail").bounding_box()
    return box is None or box["x"] + box["width"] < 0


def rail_onscreen(page) -> bool:
    box = page.locator("aside.desktop-rail").bounding_box()
    return box is not None and box["x"] >= 0


with sync_playwright() as p:
    exe = next(
        str(path)
        for path in (
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        )
        if __import__("pathlib").Path(path).is_file()
    )
    browser = p.chromium.launch(headless=False, executable_path=exe)

    # 桌面 1440：初始展开 → 点品牌收起 → 点顶条品牌开抽屉 → 遮罩关闭
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.wait_for_timeout(400)
    page.screenshot(path="docs/qa/rail-state-1-expanded.png")
    assert page.locator("aside.desktop-rail").is_visible()

    page.locator("aside.desktop-rail .rail-brand-toggle").click()
    page.wait_for_timeout(400)
    assert rail_offscreen(page)
    assert page.locator(".rail-topbar").is_visible()
    page.screenshot(path="docs/qa/rail-state-2-collapsed.png")

    page.locator(".rail-topbar .rail-brand-toggle").click()
    page.wait_for_timeout(450)
    assert rail_onscreen(page)
    assert page.locator(".rail-scrim").count() == 1
    page.screenshot(path="docs/qa/rail-state-3-drawer.png")

    page.locator(".rail-scrim").click(position={"x": 800, "y": 500})
    page.wait_for_timeout(350)
    assert rail_offscreen(page)

    # 记忆验证：刷新后仍是收起态
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(300)
    assert rail_offscreen(page)
    # 恢复展开态：抽屉态点品牌 = 固定侧栏（同时验证完整循环）
    page.locator(".rail-topbar .rail-brand-toggle").click()
    page.wait_for_timeout(400)
    page.locator("aside.desktop-rail .rail-brand-toggle").click()
    page.wait_for_timeout(300)
    assert rail_onscreen(page)
    assert page.locator(".rail-topbar").count() == 0
    print("desktop 3-state + persistence: PASS")

    # 移动端 424：无顶条无抽屉，收起默认不影响移动布局
    mobile = browser.new_page(viewport={"width": 424, "height": 956})
    mobile.goto("http://127.0.0.1:8766", wait_until="networkidle")
    mobile.wait_for_timeout(300)
    assert not mobile.locator(".rail-topbar").is_visible()
    assert mobile.locator("nav.mobile-bottom-nav").is_visible()
    mobile.screenshot(path="docs/qa/rail-state-4-mobile.png")
    print("mobile unaffected: PASS")

    browser.close()
