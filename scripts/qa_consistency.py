# -*- coding: utf-8 -*-
"""临时验证：LLM 回答与权威指南一致性（用完即弃不入库）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.qa.graph import run_qa

# (问题, 期望含有的权威口径子串, 不应出现的旧口径子串)
CASES = [
    ("每学期最多能选多少学分？", "30", "25 学分"),
    ("补考成绩怎么记载？", "补考", "60"),
    ("大学生研究计划做完有多少学分？", "4", "2000"),
    ("大一结束后怎么转专业？", "全校范围", "GPA >= 3"),
    ("缓考怎么申请？", "考前", "事后补办"),
    ("90分对应的绩点是多少？", "4.0", "3.7"),
    ("大一要上什么体育课？", "基础体育", None),
    ("形势与政策怎么考核？", "二等级制", None),
]
for q, want, banned in CASES:
    try:
        r = run_qa(q, module_signal="智能问答", student_id=None, user_profile={})
    except Exception as e:
        print(f"[FAIL] {q}: 异常 {type(e).__name__}: {e}")
        continue
    ans = (r.get("answer") or "").strip()
    tools = [t.get("tool") for t in (r.get("tool_results") or [])]
    ok = want in ans and (banned is None or banned not in ans)
    one = ans.replace("\n", " ⏎ ").replace("|", "｜")
    print(f"[{'PASS' if ok else 'FAIL'}] {q}")
    print(f"    tools={tools} | answer: {one}")
