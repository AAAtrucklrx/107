# -*- coding: utf-8 -*-
"""六步选课测试 · 第三步自动化提问脚本（第 N 轮）。

逐个向小蜗（app_test 测试实例）发送选课场景问题，等待回答完整生成，
将回答原文原样保存到 docs/选课测试_轮{N}_回答原文.md（另存截图可选）。

用法: python scripts/selection_test_round.py --round 1 --base http://localhost:8502 --shot
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DOCS = Path(__file__).resolve().parents[1] / "docs"

# 覆盖典型选课场景的测试问题集（任务示例 + 本项目能力场景）
QUESTIONS = [
    ("Q1-按专业与兴趣推荐", "我是人工智能专业的大二学生，对机器学习方向感兴趣，帮我推荐几门适合下学期选的课"),
    ("Q2-低workload筛选", "我考研没时间，推荐几门不点名、任务少的课"),
    ("Q3-课表时间冲突", "帮我看看我下学期选的课有没有上课时间冲突"),
    ("Q4-学分与培养方案匹配", "我的培养方案必修课还差多少门没修？总学分还差多少？"),
    ("Q5-先修课是否满足", "我想选机器学习这门课，需要先修哪些课？以我现在的成绩能满足先修要求吗？"),
    ("Q6-退补选建议", "根据我目前的选课情况，你觉得我下学期课业压力大吗？要不要退掉几门课？"),
    ("Q7-同课多师对比", "数学分析B1哪个老师好？"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--base", default="http://localhost:8502")
    ap.add_argument("--shot", action="store_true", help="每问截图")
    args = ap.parse_args()

    out_file = DOCS / f"选课测试_轮{args.round}_回答原文.md"
    lines: list[str] = []
    lines.append(f"# 小蜗选课测试 · 第 {args.round} 轮回答原文")
    lines.append(f"\n> 实例: {args.base}（app_test 测试版，模拟登录 PB25111691）")
    lines.append(f"> 记录时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("> 记录方式: Playwright 自动提问，等待回答完整生成后抓取 stChatMessageContent 原文\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(args.base, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(4000)

        for i, (qid, q) in enumerate(QUESTIONS, 1):
            box = page.locator('[data-testid="stChatInputTextArea"]')
            box.fill(q)
            box.press("Enter")
            print(f"[{qid}] 已提交: {q}")

            # 等待回答完整生成：消息数增加且 spinner 消失
            msgs = page.locator('[data-testid="stChatMessage"]')
            before = msgs.count()
            deadline = time.time() + 240
            ok = False
            while time.time() < deadline:
                busy = page.locator('[data-testid="stStatusWidget"]').count()
                if msgs.count() > before and busy == 0:
                    page.wait_for_timeout(2500)  # 余量等待渲染完整
                    ok = True
                    break
                page.wait_for_timeout(2000)
            if not ok:
                print(f"[{qid}] ⚠ 超时未等到完整回答")

            content = page.locator('[data-testid="stChatMessageContent"]')
            text = content.nth(content.count() - 1).inner_text().strip()
            lines.append(f"## {qid}\n")
            lines.append(f"**提问**：{q}\n")
            lines.append(f"**回答原文**（{len(text)} 字，完整生成: {'是' if ok else '否'}）：\n")
            lines.append(text + "\n")
            if args.shot:
                page.screenshot(path=str(DOCS / f"选课测试_轮{args.round}_{qid}.png"), full_page=True)
            print(f"[{qid}] 回答 {len(text)} 字 已记录")
            time.sleep(1.5)

        browser.close()

    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n记录已保存: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
