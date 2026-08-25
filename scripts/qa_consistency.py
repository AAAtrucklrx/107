# -*- coding: utf-8 -*-
"""知识库-模型调用一致性回归（需 LLM 平台可用）。

覆盖两类断言：
1. 数值/规则类问题：回答必须含权威口径（want），且不含旧口径（banned）
   ——防止 RAG 检索到旧文档或模型凭记忆输出错误数值；
2. 工具调用类问题：预期工具必须被实际调用（expect_tool）
   ——验证 AGENT 决策（确定性路由/LLM 决策）工具调用正确。

用法: python scripts/qa_consistency.py
退出码: 0=全部通过, 1=存在失败项
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (问题, 期望子串, 不应出现的子串, 期望调用的工具)
CASES = [
    ("每学期最多能选多少学分？", "30", "25 学分", None),
    ("补考成绩怎么记载？", "补考", None, None),
    ("大学生研究计划做完有多少学分？", "4", None, None),
    ("大一结束后怎么转专业？", "全校范围", None, None),
    ("缓考怎么申请？", "考前", None, None),
    # 禁词用正则：仅禁止把 90 分错误映射为 3.7（完整对照表含 85~89→3.7 属正确信息）
    ("90分对应的绩点是多少？", "4.0", re.compile(r"90.{0,6}(对应|是|→).{0,3}3\.7"), None),
    ("大一要上什么体育课？", "基础体育", None, None),
    ("形势与政策怎么考核？", "二等级制", None, None),
    # 本轮补录新增（2026-08-16 知识库补录 + 官方链接注入）
    ("本科普通专业学生一个学年的学费是多少？", "4800", None, None),  # 区分传播学4500特例，普通本科应为4800
    ("四六级怎么报名？", "neea", None, None),  # 回答须附 cet-bm.neea.edu.cn 官方报名链接（可溯源）
    # 工具调用正确性（AGENT 决策加固）
    ("推荐几门课会不会和我课表冲突？", None, None, "recommend_courses"),
    ("我要退一门课，帮我看看选课压力", None, None, "evaluate_selection_pressure"),
]

def answer_matches(ans: str, want, banned, tools: list[str], expect_tool) -> bool:
    """检查单条回答；banned 可使用普通字符串或编译后的正则。"""
    ok = True
    if want is not None:
        ok = ok and want in ans
    if banned is not None:
        if isinstance(banned, re.Pattern):
            ok = ok and banned.search(ans) is None
        else:
            ok = ok and banned not in ans
    if expect_tool is not None:
        ok = ok and expect_tool in tools
    return ok


def main() -> None:
    from agents.qa.graph import run_qa

    fails = []
    for q, want, banned, expect_tool in CASES:
        try:
            r = run_qa(q, module_signal="智能问答", student_id=None, user_profile={})
        except Exception as e:
            fails.append(q)
            print(f"[FAIL] {q}: 异常 {type(e).__name__}: {e}")
            continue
        ans = (r.get("answer") or "").strip()
        tools = [t.get("tool") for t in (r.get("tool_results") or [])]
        ok = answer_matches(ans, want, banned, tools, expect_tool)
        if not ok:
            fails.append(q)
        one = ans.replace("\n", " ⏎ ").replace("|", "｜")
        print(f"[{'PASS' if ok else 'FAIL'}] {q}" +
              (f" [工具={expect_tool}]" if expect_tool else ""))
        print(f"    tools={tools} | answer: {one[:300]}")

    total = len(CASES)
    print(f"\n结果: {total - len(fails)}/{total} 通过")
    if fails:
        print(f"失败: {fails}")
        sys.exit(1)


if __name__ == "__main__":
    main()
