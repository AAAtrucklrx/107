# -*- coding: utf-8 -*-
"""培养方案确定性路由的「清单 / 进度」意图细分回归。

背景（2026-09-15 现场定位）：`_direct_tool_route` 原先把所有含「培养方案」的问句
一律路由到 `get_my_program`（只列课程清单），而真正算完成度的
`get_program_progress` 从不被选中——它只出现在「是否已有结果」的检查列表里。

后果：问「我的培养方案完成得怎么样了」时，模型拿到的是 222 行课程清单、没有任何
已修/缺口数据，于是如实回答「没法直接算出你已经修了多少学分、还差多少」，
并把它归因成「不是你的个人培养方案」。而数据其实一直算得出来——demo 身份
实测 `percent=44.8`、必修 `23/47`、学分 `56.5/126`、4 个模块明细全都有，
是路由选错了工具。

本文件锁住细分后的行为：含进度词走 `get_program_progress`，其余走 `get_my_program`。
"""
from __future__ import annotations

from agents.qa.nodes import _PROGRAM_PROGRESS_KW, _PROGRAM_ROUTE_KW, _direct_tool_route

_PROFILE = {"major": "计算机科学与技术", "grade": "2025级"}


def _route(query: str, done: tuple[str, ...] = (), *, student_id: str = "PB25111691") -> str:
    state = {
        "student_id": student_id,
        "query": query,
        "intent": "知识问答",
        "rounds": 0,
        "user_profile": dict(_PROFILE),
        "tool_results": [{"tool": t, "status": "done"} for t in done],
    }
    decision = _direct_tool_route(state)
    if not decision:
        return "llm"
    if decision.get("decision") == "compose":
        return "compose"
    return ",".join(call["tool"] for call in (decision.get("tool_calls") or []))


def test_progress_question_routes_to_progress_tool():
    """核心回归：问「完成得怎么样」必须走 get_program_progress。"""
    assert _route("我的培养方案完成得怎么样了") == "get_program_progress"


def test_progress_keyword_variants_route_to_progress_tool():
    for query in (
        "我培养方案还差多少学分",
        "培养进度",
        "方案进度怎么样",
        "我的方案还缺几门课",
        "我培养方案的学分够毕业吗",
        "我的培养方案已经修了多少学分",
    ):
        assert _route(query) == "get_program_progress", query


def test_catalog_question_still_routes_to_my_program():
    """问清单不能被进度词带偏。"""
    for query in (
        "我的培养方案是什么",
        "培养方案里有哪些必修课",
        "帮我看看学期规划",
    ):
        assert _route(query) == "get_my_program", query


def test_non_program_question_is_not_hijacked():
    """不命中方案关键词的问句仍交 LLM 决策。"""
    assert _route("今天天气怎么样") == "llm"


def test_no_student_id_falls_through_to_llm():
    """未登录不得走个人方案确定性路由。"""
    assert _route("我的培养方案完成得怎么样了", student_id="") == "llm"


def test_wrong_tool_result_does_not_short_circuit_to_compose():
    """已有 get_my_program 结果时不能直接合成——目标工具还没跑过。

    这正是原缺陷的第二个后果：旧代码只要三者任一有结果就 compose，
    于是在同一轮里永远补不上进度数据。
    """
    assert _route("我的培养方案完成得怎么样了", done=("get_my_program",)) == "get_program_progress"
    assert _route("我的培养方案有什么课", done=("get_program_progress",)) == "get_my_program"


def test_target_tool_result_short_circuits_to_compose():
    """目标工具已有成功结果时转 compose，不再重复调用（轮1 死循环修复的既有约定）。"""
    assert _route("我的培养方案完成得怎么样了", done=("get_program_progress",)) == "compose"
    assert _route("我的培养方案有什么课", done=("get_my_program",)) == "compose"


def test_route_kw_and_progress_kw_are_disjoint():
    """守卫：进度词表不得包含方案路由词，否则细分恒真。"""
    assert _PROGRAM_PROGRESS_KW
    assert not (set(_PROGRAM_PROGRESS_KW) & set(_PROGRAM_ROUTE_KW))
