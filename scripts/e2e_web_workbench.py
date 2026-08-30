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


def assert_grid_columns(page: Page, selector: str, expected: int) -> None:
    tracks = page.locator(selector).first.evaluate(
        "node => getComputedStyle(node).gridTemplateColumns.split(' ').filter(Boolean).length",
    )
    assert tracks == expected, {"selector": selector, "expected": expected, "actual": tracks}


def assert_tiles_do_not_overlap(page: Page, selector: str) -> None:
    overlaps = page.locator(selector).evaluate_all(
        """nodes => {
          const rects = nodes
            .map((node, index) => ({ index, rect: node.getBoundingClientRect() }))
            .filter(({ rect }) => rect.width > 0 && rect.height > 0);
          const collisions = [];
          for (let left = 0; left < rects.length; left += 1) {
            for (let right = left + 1; right < rects.length; right += 1) {
              const a = rects[left].rect;
              const b = rects[right].rect;
              const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
              const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
              if (width > 1 && height > 1) collisions.push([rects[left].index, rects[right].index]);
            }
          }
          return collisions;
        }""",
    )
    assert not overlaps, {"selector": selector, "overlaps": overlaps}


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
    expect(page.get_by_role("heading", name="常见问题")).to_be_visible()
    expect(page.locator(".starter-prompt-grid .launch-tile--prompt")).to_have_count(6)
    assert_grid_columns(page, ".starter-prompt-grid", 3)
    expect(page.get_by_role("button", name="我的学业")).to_have_count(0)
    expect(page.get_by_text("知识审核", exact=True)).to_have_count(0)
    open_account_menu(page, mobile=False, authenticated=False)
    expect(page.get_by_text("匿名会话仅保存在当前浏览器", exact=True)).to_be_visible()
    expect(page.get_by_role("menuitem", name="进入演示身份")).to_have_count(0)
    page.keyboard.press("Escape")
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "anonymous-desktop-light.png"), full_page=False)

    page.locator("aside.desktop-rail").get_by_role("button", name="校园服务").click()
    page.wait_for_url(re.compile(r"/campus$"))
    expect(page.get_by_role("heading", name="常用入口")).to_be_visible()
    expect(page.locator(".campus-featured .launch-tile--link")).to_have_count(8)
    expect(page.get_by_role("heading", name="完整分类目录")).to_be_visible()
    assert_grid_columns(page, ".launch-grid--featured", 4)

    service_search = page.get_by_role("textbox", name="搜索校园服务")
    service_search.fill("图书馆")
    page.get_by_role("button", name="搜索").click()
    expect(page.get_by_role("heading", name="筛选结果")).to_be_visible()
    expect(page.get_by_role("heading", name="常用入口")).to_have_count(0)
    expect(page.get_by_role("link", name="打开 图书馆")).to_have_count(1)
    page.get_by_role("button", name="清除筛选").click()
    expect(page.locator(".campus-featured .launch-tile--link")).to_have_count(8)

    page.get_by_role("tab", name=re.compile("活动")).click()
    expect(page.get_by_role("textbox", name="搜索校园活动")).to_have_value("")
    page.get_by_role("tab", name=re.compile("办事入口")).click()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "anonymous-campus-desktop-light.png"), full_page=False)

    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(250)
    expect(page.locator("header.mobile-header").get_by_label("小蜗科大学术工作台")).to_be_visible()
    expect(page.locator("nav.mobile-bottom-nav").get_by_role("button", name="问小蜗")).to_be_visible()
    assert_grid_columns(page, ".launch-grid--featured", 2)
    closed_group = page.locator('.catalog-group--tiles[data-open="false"]').first
    expect(closed_group).to_be_visible()
    closed_category = closed_group.locator("h3").inner_text()
    stable_group = page.locator(".catalog-group--tiles").filter(
        has=page.get_by_role("heading", name=closed_category, exact=True),
    ).first
    stable_group.get_by_role("button").click()
    expect(stable_group).to_have_attribute("data-open", "true")
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "anonymous-campus-mobile-light.png"), full_page=False)

    page.locator("nav.mobile-bottom-nav").get_by_role("button", name="问小蜗").click()
    expect(page.get_by_role("heading", name="常见问题")).to_be_visible()
    assert_grid_columns(page, ".starter-prompt-grid", 1)
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
    expect(page.get_by_text("PB25111691 · 计算机科学与技术 · 2025级", exact=True)).to_be_visible()
    expect(page.get_by_text("计算机科学与技术 · 2025级", exact=True)).to_be_visible()
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
    expect(decision.get_by_role("radio", name="待定")).to_be_visible()
    expect(decision.get_by_role("radio", name="批准")).to_be_visible()
    expect(decision.get_by_role("radio", name="排除")).to_be_visible()
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
    expect(page.locator("header.mobile-header").get_by_label("小蜗科大学术工作台")).to_be_visible()
    expect(page.get_by_role("heading", name="来源规则建议")).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-review-source-mobile-dark.png"), full_page=False)

    page.get_by_role("tab", name=re.compile(r"分块 \d+")).click()
    decision = page.get_by_role("group", name=re.compile(r"分块 \d+ 审核决定")).first
    expect(decision.get_by_role("radio", name="待定")).to_be_visible()
    expect(decision.get_by_role("radio", name="批准")).to_be_visible()
    expect(decision.get_by_role("radio", name="排除")).to_be_visible()
    assert_layout(page)
    page.screenshot(path=str(SCREENSHOTS / "demo-review-chunks-mobile-dark.png"), full_page=False)
    assert not errors, errors
    context.close()


def check_surface_matrix(browser) -> None:
    viewports = (
        (1440, 1000, 3, 4),
        (1024, 768, 2, 3),
        (390, 844, 1, 2),
        (320, 740, 1, 2),
    )
    for theme in ("light", "dark"):
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme=theme)
        context.add_init_script(f"window.localStorage.setItem('xiaowo-theme', '{theme}')")
        page = context.new_page()
        errors = watch_errors(page)
        for width, height, chat_columns, campus_columns in viewports:
            page.set_viewport_size({"width": width, "height": height})
            page.goto(ANONYMOUS_URL, wait_until="networkidle")
            expect(page.locator("html")).to_have_attribute("data-theme", theme)
            expect(page.get_by_role("heading", name="常见问题")).to_be_visible()
            assert_grid_columns(page, ".starter-prompt-grid", chat_columns)
            assert_tiles_do_not_overlap(page, ".starter-prompt-grid .launch-tile")
            assert_layout(page)
            page.screenshot(
                path=str(SCREENSHOTS / f"matrix-chat-{width}-{theme}.png"),
                full_page=False,
            )

            page.goto(f"{ANONYMOUS_URL.rstrip('/')}/campus", wait_until="networkidle")
            expect(page.locator("html")).to_have_attribute("data-theme", theme)
            expect(page.locator(".campus-featured .launch-tile--link")).to_have_count(8)
            assert_grid_columns(page, ".launch-grid--featured", campus_columns)
            assert_tiles_do_not_overlap(page, ".campus-featured .launch-tile")
            assert_layout(page)
            page.screenshot(
                path=str(SCREENSHOTS / f"matrix-campus-{width}-{theme}.png"),
                full_page=False,
            )
        assert not errors, errors
        context.close()


def main() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            check_anonymous(browser)
            check_demo(browser)
            check_surface_matrix(browser)
        finally:
            browser.close()
    print(f"browser acceptance passed; screenshots: {SCREENSHOTS}")


if __name__ == "__main__":
    main()
