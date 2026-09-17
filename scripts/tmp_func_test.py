# -*- coding: utf-8 -*-
"""全模块分类问答实测（临时脚本）：九大类 ~20 问，逐问断言，输出清单。

断言类型：must=全部包含；any=任一包含；ban=不得包含。
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DOCS = Path(r"F:\小蜗\docs")
OUT = Path(r"F:\小蜗\scripts\data\tmp_func_test_answers.json")

# (分类, 问题, 断言dict)
CASES = [
    # A 知识问答（RAG）
    ("A知识", "学费多少钱？", {"must": ["4800"], "any": ["住宿费", "传播学"]}),
    ("A知识", "英语四六级怎么报名？", {"any": ["四六级", "cet-bm.neea.edu.cn", "neea"]}),
    ("A知识", "图书馆的开门时间？", {"any": ["图书馆", "开放"]}),
    # B 课业助手
    ("B课业", "我这学期有哪些课？", {"must": ["数学分析"], "any": ["14", "十四", "门课"]}),
    ("B课业", "我的GPA是多少？", {"any": ["GPA", "绩点", "3."]}),
    ("B课业", "周四晚上我有什么安排？", {"any": ["19:00", "19：00", "实验"], "ban": []}),
    # C 选课顾问
    ("C选课", "帮我推荐几门课", {"any": ["必修", "推荐", "选修"]}),
    ("C选课", "张明波老师的数学分析B2评价怎么样？", {"any": ["张明波", "9.3", "数学分析"]}),
    # D 培养方案
    ("D方案", "我的培养方案进度如何？", {"any": ["必修", "进度", "学分"]}),
    # E 日程管理
    ("E日程", "帮我明天下午3点到4点添加一个班级会议日程", {"any": ["已添加", "成功", "班级会议"]}),
    ("E日程", "我这周日程忙不忙？", {"any": ["周", "忙", "安排"]}),
    # F 活动推荐
    ("F活动", "最近有什么活动可以报名？", {"any": ["报名", "活动"], "ban": ["无法", "不可用"]}),
    ("F活动", "给我推荐一个适合我的活动", {"any": ["推荐", "活动"]}),
    # G 官方入口（强操作）
    ("G链接", "我想退掉力学B这门课", {"any": ["jw.ustc.edu.cn", "教务"]}),
    ("G链接", "评教在哪里做？", {"any": ["tqm", "评教"]}),
    ("G链接", "怎么交学费？", {"any": ["revenues", "缴费"]}),
    # H 生态工具
    ("H生态", "测试生态工具：用 eco:echo 回显「链路正常」", {"any": ["链路正常", "第三方"]}),
    # I 鲁棒性
    ("I鲁棒", "90分对应的绩点是多少？", {"must": ["4.0"], "ban": ["3.7"]}),
    ("I鲁棒", "你是谁呀", {"any": ["小蜗", "助手"]}),
]

CLOSE_DIALOG = ('[data-testid="stDialogCloseButton"]', 'button[aria-label="Close"]')


def ask(page, q):
    box = page.locator('[data-testid="stChatInputTextArea"]')
    box.fill(q)
    box.press("Enter")
    msgs = page.locator('[data-testid="stChatMessage"]')
    base = msgs.count()
    deadline = time.time() + 240
    while time.time() < deadline:
        busy = page.locator('[data-testid="stStatusWidget"]').count()
        if msgs.count() >= base + 2 and busy == 0:
            time.sleep(2)
            break
        time.sleep(2)
    c = page.locator('[data-testid="stChatMessageContent"]')
    return c.nth(c.count() - 1).inner_text()


def judge(ans, rule):
    problems = []
    for kw in rule.get("must", []):
        if kw not in ans:
            problems.append(f"缺[{kw}]")
    if rule.get("any") and not any(kw in ans for kw in rule["any"]):
        problems.append(f"无任一[{','.join(rule['any'])}]")
    for kw in rule.get("ban", []):
        if kw in ans:
            problems.append(f"含禁词[{kw}]")
    return problems


def main() -> int:
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        page = browser.new_context(viewport={"width": 1500, "height": 950}).new_page()
        page.goto("http://localhost:8502", wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(9000)
        for sel in CLOSE_DIALOG:
            try:
                page.locator(sel).first.click(timeout=2000)
                break
            except Exception:
                continue

        for i, (cat, q, rule) in enumerate(CASES, 1):
            t0 = time.time()
            try:
                ans = ask(page, q)
            except Exception as e:
                ans = f"[脚本异常] {str(e)[:80]}"
            problems = judge(ans, rule)
            ok = not problems
            results.append({"cat": cat, "q": q, "ok": ok, "problems": problems,
                            "secs": round(time.time() - t0, 1), "answer": ans[:600]})
            print(f"[{'PASS' if ok else 'FAIL'}] {cat} {q}  ({results[-1]['secs']}s)"
                  + (f"  {problems}" if problems else ""))
            OUT.write_text(__import__("json").dumps(results, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        page.screenshot(path=str(DOCS / "tmp_func_test_final.png"), full_page=True)
        browser.close()

    n_pass = sum(1 for r in results if r["ok"])
    print(f"\n总计: {n_pass}/{len(results)} 通过；失败清单：")
    for r in results:
        if not r["ok"]:
            print(f"  - [{r['cat']}] {r['q']} → {r['problems']}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
