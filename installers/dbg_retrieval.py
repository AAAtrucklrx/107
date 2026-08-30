# -*- coding: utf-8 -*-
"""本地验证 ② 查询发散 + ③ 检索步骤记录(不依赖 LLM 平台)。"""
import io, sys
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.qa.nodes import act, embedding_parse

# ③: embedding_parse 首轮检索记录
st = {"query": "学生证怎么补办", "module_signal": "智能问答", "intent": "", "intent_top3": [],
      "candidates": [], "candidates_found": False, "retrieval_log": []}
out = embedding_parse(st)
log1 = out.get("retrieval_log") or []
print("③ embedding_parse retrieval_log:", log1)
print("   候选:", len(out.get("candidates") or []), "条 | found:", out.get("candidates_found"))
assert len(log1) == 1 and log1[0]["decision"] == "retrieve" and "首轮检索" in log1[0]["reason"]

# ②+③: act retrieve 发散多个子查询并记录
st2 = {"query": "退学和休学分别怎么办", "decision": "retrieve", "rounds": 1,
       "retrieve_query": "退学 休学 办理流程",
       "sub_queries": ["退学 办理流程", "休学 办理流程"],
       "candidates": list(out.get("candidates") or []),
       "candidates_found": out.get("candidates_found", False),
       "retrieval_log": list(log1)}
out2 = act(st2)
log2 = out2.get("retrieval_log") or []
print("\n② act retrieve 记录:", log2)
print("   总候选:", len(out2.get("candidates") or []), "条")
assert len(log2) == 1 + 3, f"应为 1 首轮 + 3 子查询 = 4 条, 实际 {len(log2)}"
assert all("重新检索" in e["reason"] for e in log2[1:])
assert out2["rounds"] == 2

# ③: 单子查询(无 sub_queries)也正常
st3 = {"query": "医保报销", "decision": "retrieve", "rounds": 2,
       "retrieve_query": "医保 报销流程", "sub_queries": [],
       "candidates": [], "candidates_found": False, "retrieval_log": []}
out3 = act(st3)
print("\n单查询检索记录:", out3.get("retrieval_log"))
assert len(out3.get("retrieval_log") or []) == 1

print("\n结果: PASS ✅ (②查询发散 + ③检索步骤记录)")
