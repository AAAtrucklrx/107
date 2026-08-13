"""
小蜗 — QA LangGraph 装配与入口
流程: embedding_parse(意图分类+候选召回双通道) → think(LLM 自主决策, 循环 ≤4 轮)
      → act(执行检索/工具) → compose(统一组织回答)
think 决策: clarify → 结束追问; compose → 合成; 否则循环回 act
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.qa.nodes import act, compose, embedding_parse, think
from agents.qa.state import QaState
from utils.logger import get_logger

log = get_logger("xiaowo.qa.graph")

MAX_ROUNDS = 4


def _route_after_think(state: QaState) -> str:
    """think 决策路由：compose/clarify/超轮次 → 结束；否则 → act 执行"""
    decision = state.get("decision") or "compose"
    rounds = state.get("rounds") or 0

    if decision == "compose":
        return "compose"
    if decision == "clarify":
        return "end"
    if rounds >= MAX_ROUNDS:
        log.info(f"达到轮次上限 {MAX_ROUNDS}，强制进入合成")
        return "compose"
    return "act"


def build_graph():
    """装配 QA 状态机并编译"""
    g = StateGraph(QaState)
    g.add_node("embedding_parse", embedding_parse)
    g.add_node("think", think)
    g.add_node("act", act)
    g.add_node("compose", compose)

    g.add_edge(START, "embedding_parse")
    g.add_edge("embedding_parse", "think")
    g.add_conditional_edges(
        "think",
        _route_after_think,
        {"act": "act", "compose": "compose", "end": END},
    )
    g.add_edge("act", "think")
    g.add_edge("compose", END)

    return g.compile()


_compiled_graph = None


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def _ensure_services():
    """确保服务容器已初始化（应用启动时已初始化则直接复用）"""
    from services.service_container import ServiceContainer
    sc = ServiceContainer()
    try:
        sc.db
        sc.faq_store
    except RuntimeError:
        from config import CHROMA_PERSIST_DIR, DATABASE_PATH, KNOWLEDGE_DATA_DIR, SCHEMA_PATH
        from database.seed_data import SEED_SQL
        sc.init_database(DATABASE_PATH, SCHEMA_PATH, seed_sql=SEED_SQL)
        sc.init_vector_store(CHROMA_PERSIST_DIR, knowledge_data_dir=KNOWLEDGE_DATA_DIR)
    return sc


def run_qa(query: str, module_signal: str = "自动判断",
           student_id: str = None, user_profile: dict = None,
           chat_history: list[dict] = None) -> dict:
    """
    统一问答入口（替换原 router/agent 分发）。

    Args:
        query: 用户原始问题
        module_signal: 侧边栏模块信号（"自动判断" 或模块名，仅作软提示）
        student_id: 学号（登录用户；未登录为空）
        user_profile: 用户信息（姓名/专业/年级等）
        chat_history: 最近对话历史 [{role, content}, ...]（多轮指代理解）

    Returns:
        {"answer": str, "clarify_question": str, "intent": str, "decision": str,
         "tool_results": [...], "rounds": int, "thought_log": [...], "error": str}
    """
    try:
        _ensure_services()
        initial: QaState = {
            "query": query,
            "module_signal": module_signal or "自动判断",
            "intent": "",
            "intent_top3": [],
            "candidates": [],
            "candidates_found": False,
            "student_id": student_id or "",
            "user_profile": user_profile or {},
            "chat_history": chat_history or [],
            "decision": "compose",
            "retrieve_query": "",
            "tool_calls": [],
            "tool_results": [],
            "rounds": 0,
            "thought_log": [],
            "clarify_question": "",
            "answer": "",
            "error": "",
        }
        final = _get_graph().invoke(initial)
        # clarify 决策时追问文本作为回答透出（计划：clarify 返回追问文本，结束）
        if final.get("decision") == "clarify" and not (final.get("answer") or "").strip():
            final["answer"] = final.get("clarify_question") or "请问能再具体一些吗？"
        return final
    except Exception as e:
        log.error(f"QA 流程执行失败: {e}")
        return {
            "query": query,
            "module_signal": module_signal or "自动判断",
            "intent": "",
            "intent_top3": [],
            "candidates": [],
            "candidates_found": False,
            "student_id": student_id or "",
            "user_profile": user_profile or {},
            "chat_history": chat_history or [],
            "decision": "compose",
            "retrieve_query": "",
            "tool_calls": [],
            "tool_results": [],
            "rounds": 0,
            "thought_log": [],
            "clarify_question": "",
            "answer": f"处理请求时出错：{e}",
            "error": str(e),
        }
