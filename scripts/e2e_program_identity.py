# -*- coding: utf-8 -*-
"""Local UI regression for authenticated program identity and fallback display."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SHOT = ROOT / "docs" / "e2e_program_identity_desktop.png"
MOBILE_SHOT = ROOT / "docs" / "e2e_program_identity_mobile.png"


def _close_dialogs(page: Page) -> None:
    for _ in range(3):
        if page.locator('[role="dialog"]').count() == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)


def _assert(failures: list[str], label: str, condition: bool, detail="") -> None:
    marker = "PASS" if condition else "FAIL"
    print(f"[{marker}] {label}{f' - {detail}' if detail else ''}")
    if not condition:
        failures.append(label)


def _assert_no_horizontal_overflow(page: Page, failures: list[str], label: str) -> None:
    metrics = page.evaluate(
        """() => ({
            viewport: window.innerWidth,
            document: document.documentElement.scrollWidth,
            overflowingButtons: [...document.querySelectorAll('button')]
                .filter((el) => {
                    const box = el.getBoundingClientRect();
                    const visible = box.right > 0 && box.left < window.innerWidth
                        && box.bottom > 0 && box.top < window.innerHeight;
                    return visible && el.scrollWidth > el.clientWidth + 1;
                })
                .map((el) => el.innerText.trim()).filter(Boolean).slice(0, 10)
        })"""
    )
    _assert(
        failures,
        f"{label}页面无横向溢出",
        metrics["document"] <= metrics["viewport"] + 1,
        metrics,
    )
    _assert(
        failures,
        f"{label}按钮文字未溢出",
        not metrics["overflowingButtons"],
        metrics["overflowingButtons"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8503")
    args = parser.parse_args()
    failures: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=EDGE, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(args.base, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(2_000)
        _close_dialogs(page)

        sidebar = page.locator('[data-testid="stSidebar"]')
        sidebar.get_by_text("培养方案", exact=True).click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2_000)
        _close_dialogs(page)

        main_area = page.locator('[data-testid="stMainBlockContainer"]')
        main_area.get_by_text("专业通用参考，不是个人培养方案", exact=True).wait_for(
            timeout=60_000
        )
        main_text = main_area.inner_text()
        sidebar_text = sidebar.inner_text()

        required_main = (
            "测试",
            "学号 PB25111691",
            "专业 人工智能",
            "年级 2025级",
            "身份来源：本地测试数据备份",
            "专业通用参考，不是个人培养方案",
        )
        for text in required_main:
            _assert(failures, f"主区显示{text}", text in main_text)
        _assert(failures, "侧栏显示当前用户年级", "年级: 2025级" in sidebar_text)
        _assert(
            failures,
            "培养方案主标题不重复",
            main_area.get_by_text("培养方案", exact=True).count() == 1,
            main_area.get_by_text("培养方案", exact=True).count(),
        )

        expected_tabs = ["我的方案", "学期规划", "进度概览"]
        tab_labels = [
            label for label in expected_tabs
            if main_area.get_by_text(label, exact=True).count() == 1
        ]
        _assert(
            failures,
            "培养方案三标签齐全",
            tab_labels == expected_tabs,
            tab_labels,
        )

        button_labels = main_area.locator("button").all_inner_texts()
        banned_actions = [
            label for label in button_labels
            if label.strip() in {"推荐", "查冲突", "加日程"}
        ]
        ask_buttons = [label for label in button_labels if label.strip().startswith("💬")]
        _assert(failures, "旧三联动按钮已移除", not banned_actions, banned_actions)
        _assert(failures, "课程保留问问小蜗入口", bool(ask_buttons), len(ask_buttons))

        page.screenshot(path=str(DESKTOP_SHOT), full_page=True)
        _assert_no_horizontal_overflow(page, failures, "桌面端")

        tab_content = {
            "学期规划": "选择学年",
            "进度概览": "培养进度",
            "我的方案": "方案课程学分合计",
        }
        for tab_label, expected_text in tab_content.items():
            main_area.get_by_text(tab_label, exact=True).click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1_000)
            _close_dialogs(page)
            main_area = page.locator('[data-testid="stMainBlockContainer"]')
            _assert(
                failures,
                f"标签可切换到{tab_label}",
                expected_text in main_area.inner_text(),
                expected_text,
            )

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(1_000)
        collapse = page.locator('[data-testid="stSidebarCollapseButton"] button')
        sidebar_box = sidebar.bounding_box()
        if collapse.count() and sidebar_box and sidebar_box["x"] >= 0:
            collapse.first.evaluate("element => element.click()")
            page.wait_for_timeout(500)
        page.screenshot(path=str(MOBILE_SHOT), full_page=True)
        _assert_no_horizontal_overflow(page, failures, "移动端")

        browser.close()

    if failures:
        print(f"结果: {len(failures)} 项失败 - {failures}")
        return 1
    print("结果: 培养方案身份与响应式 UI 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
