# -*- coding: utf-8 -*-
"""智能体链路冒烟: QA 全链路跑 5 类典型问题, 校验结构化回答与工具调用次数

用法: python scripts/tmp_advisor_smoke.py
退出码: 0=全部通过, 1=存在失败项
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.qa.graph import run_qa

SCENES = [
    ("推荐", "我是大二学生，想保研，推荐几门给分好的课"),
    ("低 workload", "考研没时间，推荐不点名、任务少的课"),
    ("教师对比", "数学分析(B1)哪个老师好？"),
    ("澄清", "推荐课"),
    ("FAQ", "学生证丢了怎么补办？"),
]

FAILS = []

for name, query in SCENES:
    try:
        r = run_qa(query, module_signal="选课顾问", student_id=None, user_profile={})
    except Exception as e:
        FAILS.append(name)
        print(f"[FAIL] {name}: 异常堆栈 {type(e).__name__}: {e}")
        continue
    answer = (r.get("answer") or "").strip()
    tool_calls = [t for t in (r.get("tool_results") or [])]
    n_calls = len(tool_calls)
    ok = bool(answer) and n_calls <= 4
    if not ok:
        FAILS.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: intent={r.get('intent')} "
          f"decision={r.get('decision')} 工具调用={n_calls} 轮次={r.get('rounds')} "
          f"回答长度={len(answer)}")
    tools = [t.get("tool") for t in tool_calls]
    if tools:
        print(f"      工具: {tools}")
    print(f"      回答开头: {answer[:120].replace(chr(10), ' | ')}")

print(f"\n结果: {len(SCENES) - len(FAILS)}/{len(SCENES)} 通过")
if FAILS:
    print(f"失败: {FAILS}")
    sys.exit(1)
