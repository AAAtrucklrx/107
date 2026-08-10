"""小蜗开发流水线 — 图编排（LangGraph StateGraph）。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import (
    canvas_node,
    collect_context,
    decide_node,
    execute_node,
    plan_node,
    report_node,
    test_node,
)
from .state import PipelineState


def _route_after_execute(state: dict) -> str:
    """执行后进入测试。"""
    return "test"


def _route_after_decide(state: dict) -> str:
    """决策路由：通过 → 报告；失败且未超轮次 → 再执行；失败且超轮次 → 报告。"""
    return "report"


def build_graph():
    g = StateGraph(PipelineState)

    g.add_node("collect_context", collect_context)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("test", test_node)
    g.add_node("report", report_node)
    g.add_node("canvas", canvas_node)
    g.add_node("decide", decide_node)

    g.add_edge(START, "collect_context")
    g.add_edge("collect_context", "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", "test")

    # 循环：decide 失败且未达上限 → execute（进入下一轮）
    # 注意：round 递增由 decide_node 在 fail 时返回（条件边不支持 state 更新）
    def decide_route(state: dict) -> str:
        if state.get("verdict") == "pass":
            return "report"
        round_no = int(state.get("round", 1))
        max_rounds = int(state.get("max_rounds", config_default_rounds()))
        if round_no < max_rounds:
            return "execute"
        return "report"

    g.add_conditional_edges("test", lambda s: "decide", {"decide": "decide"})
    g.add_conditional_edges("decide", decide_route, {"execute": "execute", "report": "report"})

    g.add_edge("report", "canvas")
    g.add_edge("canvas", END)

    return g.compile()


def config_default_rounds() -> int:
    from .config import DEFAULT_MAX_ROUNDS

    return DEFAULT_MAX_ROUNDS


def run_pipeline(task: str, executor: str = "claude", max_rounds: int | None = None) -> dict:
    """编译并运行流水线，返回最终状态。"""
    from .config import DEFAULT_MAX_ROUNDS

    graph = build_graph()
    initial = {
        "task": task,
        "executor": executor,
        "max_rounds": max_rounds or DEFAULT_MAX_ROUNDS,
        "round": 1,
        "changes": [],
        "issues": [],
        "verdict": "fail",
    }
    return graph.invoke(initial)
