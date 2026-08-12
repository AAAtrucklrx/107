# -*- coding: utf-8 -*-
"""临时: 浏览器端到端实测 — 5 场景截图到 docs/（playwright + 系统 Edge）"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BASE = "http://localhost:8501"
DOCS = Path(__file__).resolve().parents[1] / "docs"

SCENES = [
    ("e2e_v3_home", None, None),
    ("e2e_v3_q1_recommend", "选课顾问", "我是大二学生，想保研，推荐几门给分好的课"),
    ("e2e_v3_q2_low_workload", "选课顾问", "考研没时间，推荐不点名、任务少的课"),
    ("e2e_v3_q3_teacher_compare", "选课顾问", "数学分析(B1)哪个老师好？"),
    ("e2e_v3_q4_clarify", "选课顾问", "推荐课"),
    ("e2e_v3_q5_faq", "智能问答", "学生证丢了怎么补办？"),
]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(BASE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(DOCS / "e2e_v3_home.png"), full_page=True)
        print("[OK] home 截图")

        before = page.locator('[data-testid="stChatMessage"]').count()

        for name, module, query in SCENES[1:]:
            # 切模块（radio）
            if module:
                page.locator(f'[data-testid="stSidebar"] label:has-text("{module}")').first.click()
                page.wait_for_timeout(800)
            # 清空对话
            btn = page.locator('button:has-text("清空对话")')
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(800)
            before = page.locator('[data-testid="stChatMessage"]').count()
            # 输入并提交
            box = page.locator('[data-testid="stChatInputTextArea"]')
            box.fill(query)
            box.press("Enter")
            print(f"[{name}] 已提交: {query}")
            # 等待回答完成（消息数增加且 spinner 消失）
            ok = False
            deadline = time.time() + 180
            while time.time() < deadline:
                n = page.locator('[data-testid="stChatMessage"]').count()
                busy = page.locator('[data-testid="stStatusWidget"], [data-testid="stSpinner"]').count()
                if n > before and busy == 0:
                    page.wait_for_timeout(2000)
                    ok = True
                    break
                page.wait_for_timeout(2000)
            if not ok:
                print(f"[{name}] 超时未等到回答")
            # 截图（full_page 保证回答完整可见）
            page.screenshot(path=str(DOCS / f"{name}.png"), full_page=True)
            print(f"[OK] {name} 截图完成" + ("" if ok else " (超时)"))
            # 读取回答完整文本（验证内容与规则混入）
            msgs = page.locator('[data-testid="stChatMessage"]')
            last = msgs.nth(msgs.count() - 1)
            text = last.inner_text()
            print(f"     回答长度: {len(text)}")
            print(f"     回答开头: {text[:500].replace(chr(10), ' | ')}")
            # 抓 DOM 检查 smart_toy 来源
            html = last.inner_html()
            idx = html.find("smart_toy")
            print(f"     DOM 含 smart_toy: {idx >= 0}" + (f" (位置 {idx})" if idx >= 0 else ""))

        browser.close()


if __name__ == "__main__":
    main()
