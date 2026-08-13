# -*- coding: utf-8 -*-
"""智能体链路冒烟: QA 全链路跑 5 类典型问题, 校验结构化回答与工具调用次数

每个场景为 (名称, 问题, expected) 三元组:
  - expected=None : 仅校验回答非空 + 工具调用 ≤4
  - expected=子串 : 额外要求该子串出现在回答中（否则判 FAIL）

教师对比场景"数学分析(B1)哪个老师好？"归一化后精确命中 B1 班型, 直接列出教师口碑;
数分歧义场景"数分哪个老师好？"由 LLM 解析简称→归一化匹配多班型→ambiguity 反问,
故仅校验回答中出现"数学分析"这个稳定课程名, 不做整句硬匹配（LLM 输出为概率性）。

用法: python scripts/advisor_smoke.py
退出码: 0=全部通过, 1=存在失败项
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.qa.graph import run_qa

SCENES = [
    ("推荐", "我是大二学生，想保研，推荐几门给分好的课", None),
    ("低 workload", "考研没时间，推荐不点名、任务少的课", None),
    ("教师对比", "数学分析(B1)哪个老师好？", "数学分析"),
    ("数分歧义反问", "数分哪个老师好？", "数学分析"),
    ("澄清", "推荐课", None),
    ("FAQ", "学生证丢了怎么补办？", "补办"),
]

FAILS = []

for name, query, expected in SCENES:
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
    # expected 非 None 时, 要求该子串出现在回答中（LLM 概率性输出, 仅做宽松子串匹配）
    if ok and expected is not None:
        ok = expected in answer
    if not ok:
        FAILS.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: intent={r.get('intent')} "
          f"decision={r.get('decision')} 工具调用={n_calls} 轮次={r.get('rounds')} "
          f"回答长度={len(answer)}" + (f" 校验「{expected}」" if expected is not None else ""))
    tools = [t.get("tool") for t in tool_calls]
    if tools:
        print(f"      工具: {tools}")
    print(f"      回答开头: {answer[:120].replace(chr(10), ' | ')}")

print(f"\n结果: {len(SCENES) - len(FAILS)}/{len(SCENES)} 通过")
if FAILS:
    print(f"失败: {FAILS}")
    sys.exit(1)
