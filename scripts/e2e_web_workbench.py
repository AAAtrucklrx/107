"""Desktop/mobile, light/dark, anonymous/demo browser acceptance checks."""

from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


ANONYMOUS_URL = os.environ.get("XIAOWO_E2E_ANONYMOUS_URL", "http://127.0.0.1:8766")
DEMO_URL = os.environ.get("XIAOWO_E2E_DEMO_URL", "http://127.0.0.1:8765")
SCREENSHOTS = Path(
    os.environ.get("XIAOWO_E2E_SCREENSHOTS", "scripts/data/tmp_test/web_e2e/screenshots"),
).resolve()


def launch_browser(playwright):
    explicit = os.environ.get("XIAOWO_E2E_BROWSER_EXECUTABLE", "").strip()
    if explicit:
        return playwright.chromium.launch(headless=True, executable_path=explicit)
    system_browsers = (
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    )
    installed = next((path for path in system_browsers if path.is_file()), None)
    if installed is not None:
        return playwright.chromium.launch(headless=True, executable_path=str(installed))
    return playwright.chromium.launch(headless=True)


def assert_layout(page: Page) -> None:
    metrics = page.evaluate(
        """() => ({
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: document.documentElement.clientWidth,
          bodyWidth: document.body.scrollWidth,
          canvas: (() => {
            const node = document.querySelector('.workspace-canvas');
            if (!node) return null;
            const rect = node.getBoundingClientRect();
            return { width: rect.width, height: rect.height, left: rect.left, right: rect.right };
          })(),
        })""",
    )
    assert metrics["documentWidth"] <= metrics["viewportWidth"] + 1, metrics
    assert metrics["bodyWidth"] <= metrics["viewportWidth"] + 1, metrics
    assert metrics["canvas"] and metrics["canvas"]["width"] > 250 and metrics["canvas"]["height"] > 300, metrics
    assert metrics["canvas"]["left"] >= -1 and metrics["canvas"]["right"] <= metrics["viewportWidth"] + 1, metrics


def watch_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(f"console: {message.text}") if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    return errors


def open_account_menu(page: Page, *, mobile: bool, authenticated: bool) -> None:
    root = page.locator("header.mobile-header") if mobile else page.locator("aside.desktop-rail")
    label = re.compile("测试|PB25111691") if authenticated else re.compile("未登录")
    root.get_by_role("button", name=label).click()


def check_anonymous(browser) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme="light")
    page = context.new_page()
    errors = watch_errors(page)
    page.goto(ANONYMOUS_URL, wait_until="networkidle")
    expect(page.locator("aside.desktop-rail").get_by_label("小蜗科大学术工作台")).to_be_visible()
    expect(page.get_by_role("textbox", name="向小蜗提问")).to_be_visible()
    expect(page.get_by_role("button", name="我的学业")).to_have_count(0)
    expect(page.get_by_text("知识审核", exact=True)).to_have_count(0)
    open_account_menu(page, mobile=False, authenticated=False)
    expect(page.get_by_text("匿名会话仅保存在当前浏览器", exact=True)).to_be_visible()
    expect(page.get_by_role("menuitem", name="进入演示身份")).to_have_count(0)
    page.keyboard.press("Escape")
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "anonymous-desktop-light.png"), full_page=False)

    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="networkidle")
    expect(page.locator("header.mobile-header").get_by_alt_text("小蜗")).to_be_visible()
    expect(page.locator("nav.mobile-bottom-nav").get_by_role("button", name="问小蜗")).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "anonymous-mobile-light.png"), full_page=False)
    assert not errors, errors
    context.close()


def check_demo(browser) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme="light")
    page = context.new_page()
    errors = watch_errors(page)
    page.goto(DEMO_URL, wait_until="networkidle")
    open_account_menu(page, mobile=False, authenticated=False)
    page.get_by_role("menuitem", name="进入演示身份").click()
    page.wait_for_url(re.compile(r"/academic$"))
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="我的学业")).to_be_visible()
    expect(page.get_by_text("PB25111691 · 人工智能 · 2025级", exact=True)).to_be_visible()
    expect(page.get_by_text("人工智能 · 2025级", exact=True)).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-academic-desktop-light.png"), full_page=False)

    page.get_by_role("tab", name=re.compile("培养方案")).click()
    expect(page.get_by_text("演示数据：合成个人培养方案", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="问问小蜗").first).to_be_visible()
    expect(page.get_by_text("查冲突", exact=True)).to_have_count(0)
    expect(page.get_by_text("加日程", exact=True)).to_have_count(0)

    open_account_menu(page, mobile=False, authenticated=True)
    page.get_by_role("menuitem", name="知识审核").click()
    page.wait_for_url(re.compile(r"/review$"))
    expect(page.get_by_role("heading", name="知识审核")).to_be_visible()
    expect(page.get_by_text("演示审核与生产知识永久隔离", exact=True)).to_be_visible()
    page.get_by_role("tab", name="发布治理").click()
    expect(page.get_by_role("heading", name="发布状态")).to_be_visible()
    expect(page.get_by_text("演示索引", exact=True)).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-review-governance-desktop-light.png"), full_page=False)

    page.get_by_role("tab", name="内容队列").click()
    page.locator(".review-queue-item").first.click()
    page.get_by_role("tab", name=re.compile(r"分块 \d+")).click()
    decision = page.get_by_role("group", name=re.compile(r"分块 \d+ 审核决定")).first
    expect(decision.get_by_role("button", name="待定")).to_be_visible()
    expect(decision.get_by_role("button", name="批准")).to_be_visible()
    expect(decision.get_by_role("button", name="排除")).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-review-chunks-desktop-light.png"), full_page=False)

    expect(page.get_by_role("tab", name="来源治理")).to_be_visible()
    page.get_by_role("tab", name="来源治理").click()
    expect(page.get_by_role("heading", name="来源规则建议")).to_be_visible()
    expect(page.get_by_label("精确域名")).not_to_have_value("")
    page.screenshot(path=str(SCREENSHOTS / "demo-review-source-desktop-light.png"), full_page=False)

    open_account_menu(page, mobile=False, authenticated=True)
    page.get_by_role("menuitem", name="深色主题").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-review-source-desktop-dark.png"), full_page=False)

    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(250)
    expect(page.locator("header.mobile-header").get_by_alt_text("小蜗")).to_be_visible()
    expect(page.get_by_role("heading", name="来源规则建议")).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-review-source-mobile-dark.png"), full_page=False)

    page.get_by_role("tab", name=re.compile(r"分块 \d+")).click()
    decision = page.get_by_role("group", name=re.compile(r"分块 \d+ 审核决定")).first
    expect(decision.get_by_role("button", name="待定")).to_be_visible()
    expect(decision.get_by_role("button", name="批准")).to_be_visible()
    expect(decision.get_by_role("button", name="排除")).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-review-chunks-mobile-dark.png"), full_page=False)
    assert not errors, errors
    context.close()


def main() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            check_anonymous(browser)
            check_demo(browser)
        finally:
            browser.close()
    print(f"browser acceptance passed; screenshots: {SCREENSHOTS}")


if __name__ == "__main__":
    main()
