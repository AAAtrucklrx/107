# -*- coding: utf-8 -*-
"""小蜗全模块交互测试 · 第 N 轮（用户验收迭代方法论）。

通过 run_qa 直连 QA 链路提问（与 app.py::process_query 同一链路，回答与
Streamlit 界面一致；沙箱环境无法运行浏览器自动化，Playwright 驱动子进程
管道被沙箱拦截），记录回答原文到 docs/交互测试_轮{N}_回答原文.md。

用法: python scripts/test_round_full.py --round 1
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DOCS = Path(__file__).resolve().parents[1] / "docs"

# ── 全模块交互问题集（知识问答 / 课业助手 / 选课顾问 / 日程管理 / 复合）──
QUESTIONS = [
    # 知识问答（权威资料库）
    ("Q1-学分上限", "每学期最多能选多少学分？"),
    ("Q2-GPA换算", "90分对应的绩点是多少？"),
    ("Q3-缓考申请", "缓考怎么申请？需要什么材料？"),
    ("Q4-秋季校历", "2026年秋季学期什么时候开学？"),
    ("Q5-体测毕业", "体质测试不及格会影响毕业吗？"),
    ("Q6-转专业", "大一结束后怎么转专业？"),
    # 课业助手
    ("Q7-成绩查询", "帮我查一下我的成绩"),
    ("Q8-GPA计算", "我的GPA是多少？"),
    ("Q9-课表查询", "我这周有什么课？"),
    # 选课顾问（新能力）
    ("Q10-课程推荐", "我是人工智能专业的大二学生，对机器学习方向感兴趣，帮我推荐几门课"),
    ("Q11-冲突检测", "我选的课有没有时间冲突？"),
    ("Q12-退补选压力", "我学分是不是超了，帮我看看要不要退课"),
    ("Q13-教师对比", "数学分析(B1)哪个老师好？"),
    # 日程管理
    ("Q14-日程查询", "我今天有什么安排？"),
    ("Q15-添加日程", "帮我添加一个日程，明天下午3点开组会"),
    # 复合问题
    ("Q16-复合GPA推荐", "查一下我的GPA，然后根据我的成绩推荐几门适合我的课"),
]


def _setup_test_mode() -> None:
    """伪登录 PB25111691（与 app_test._init_test_mode 一致），离线模式走本地数据"""
    from config import DATABASE_PATH, SCHEMA_PATH
    from services.service_container import ServiceContainer
    from services.session_ctx import set_student, reset_student

    sc = ServiceContainer()
    sc.init_database(DATABASE_PATH, SCHEMA_PATH)
    tok = set_student("PB25111691")
    c = sc.cas_client
    c._logged_in = True
    c._student_id = "PB25111691"
    import tools.course_tools as ct
    ct.set_offline_mode(True)
    reset_student(tok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    args = ap.parse_args()

    _setup_test_mode()

    from agents.qa.graph import run_qa

    out_file = DOCS / f"交互测试_轮{args.round}_回答原文.md"
    lines: list[str] = []
    lines.append(f"# 小蜗全模块交互测试 · 第 {args.round} 轮回答原文")
    lines.append(f"\n> 实例: app_test 测试版（模拟登录 PB25111691），run_qa 直连 QA 链路")
    lines.append(f"> 记录时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("> 记录方式: run_qa 直连（与 app.py::process_query 同一链路；沙箱内无法运行")
    lines.append("> Playwright 浏览器自动化——驱动子进程管道被沙箱拦截，回答内容与界面一致）\n")

    fails = []
    for qid, q in QUESTIONS:
        t0 = time.time()
        try:
            r = run_qa(q, module_signal="智能问答", student_id="PB25111691",
                       user_profile={"name": "测试", "major": "人工智能",
                                     "grade": "2025级", "logged_in": True})
            ans = (r.get("answer") or r.get("clarify_question") or "").strip()
            error = r.get("error") or ""
        except Exception as e:
            ans = f"⚠ 异常: {type(e).__name__}: {e}"
            error = ""
        elapsed = int(time.time() - t0)
        tools = [t.get("tool") for t in (r.get("tool_results") or [])] if 'r' in dir() else []
        intent = r.get("intent", "") if 'r' in dir() else ""
        decision = r.get("decision", "") if 'r' in dir() else ""
        rounds = r.get("rounds", "") if 'r' in dir() else ""
        if not ans or error:
            fails.append(qid)

        lines.append(f"## {qid}（耗时 {elapsed}s）\n")
        lines.append(f"**问**: {q}\n")
        lines.append(f"**链路**: intent={intent} | decision={decision} | rounds={rounds} | tools={tools}\n")
        lines.append(f"**答**:\n\n{ans}\n")
        print(f"[{qid}] intent={intent} tools={tools} ({elapsed}s, {len(ans)} 字)")

    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已保存: {out_file}")
    if fails:
        print(f"⚠ 异常/空回答: {fails}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
