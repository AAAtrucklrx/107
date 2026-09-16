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

from pathlib import Path

import pytest

from agents.qa.nodes import (
    _PROGRAM_PROGRESS_KW,
    _PROGRAM_ROUTE_KW,
    _direct_tool_route,
    _next_term_year_index,
)

_DB = Path(__file__).resolve().parents[2] / "data" / "course_data.db"

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


# ── 跨专业对比 / 转专业（2026-09-15 新增）──
#
# 背景：用户问「我是计算机系的，大三想转去物理学院，帮我对比下我目前选的课和物理学院的
# 培养方案」。旧路由一律按本人专业查 get_my_program，卡片给的是**自己**的方案，模型拿不到
# 物理方案只能如实说「没有物理学院数据」——而方案库里其实有（物理学 2025级 70 门）。

_COLLEGES = ["计算机科学与技术学院", "物理学院", "数学科学学院", "人工智能与数据科学学院"]


@pytest.fixture
def colleges(monkeypatch):
    """固定学院名单，使路由测试不依赖方案库。"""
    import agents.qa.nodes as nodes

    monkeypatch.setattr(nodes, "_KNOWN_COLLEGE_CACHE", list(_COLLEGES))
    return _COLLEGES


def _st(query: str, done: tuple[str, ...] = ()) -> dict:
    return {
        "student_id": "PB25111691", "query": query, "intent": "知识问答", "rounds": 0,
        "user_profile": {"major": "计算机科学与技术", "grade": "2025级"},
        "tool_results": [{"tool": t, "status": "done"} for t in done],
    }


def test_same_major_handles_containment():
    from agents.qa.nodes import _same_major

    assert _same_major("计算机科学与技术", "计算机科学与技术")
    assert _same_major("计算机", "计算机科学与技术")
    assert _same_major("", "物理")  # 任一为空 → 视为同一（保持既有行为）
    assert not _same_major("物理学院", "计算机科学与技术")


def test_detect_target_college_skips_own_college(colleges):
    from agents.qa.nodes import _detect_target_college

    q = "我是计算机系的，大三想转去物理学院，帮我对比下我目前选的课和物理学院的培养方案"
    assert _detect_target_college(q, "计算机科学与技术") == "物理学院"
    # 只提到本人学院时，不得把自己当成目标
    assert _detect_target_college("计算机科学与技术学院的培养方案", "计算机科学与技术") is None


def test_cross_major_question_routes_to_target_program(colleges):
    """核心回归：跨专业对比必须查目标专业，且走进度工具（才能算「哪些能抵、还差哪些」）。"""
    q = ("我是计算机系的，大三想转去物理学院，帮我对比下我目前选的课和物理学院的培养方案，"
         "我下个学期应该选哪些课来拉近差距和过度呢？")
    d = _direct_tool_route(_st(q))
    calls = d["tool_calls"]
    # 问句同时要「下个学期选哪些课」→ 额外并行调 plan_semester，按真实学期分组
    assert [c["tool"] for c in calls] == ["get_program_progress", "plan_semester"]
    assert calls[0]["args"]["major"] == "物理学院"
    assert calls[1]["args"]["major"] == "物理学院", "排课也必须查目标专业，不得混入本人方案"
    assert calls[1]["args"]["year_index"] == _next_term_year_index("2025级")
    assert "跨专业方案对比" in d["thought_log"][-1]["reason"]


def test_cross_major_without_course_pick_skips_plan_semester(colleges):
    """只是对比/转专业、没问「下个学期选什么」时，不额外排课。"""
    d = _direct_tool_route(_st("我想转去物理学院，对比一下物理学院的培养方案"))
    assert [c["tool"] for c in d["tool_calls"]] == ["get_program_progress"]


def test_comparison_without_target_college_falls_back_to_own_major(colleges):
    """提了「对比」但没说别的学院 → 不瞎猜目标，退回本人专业既有行为。"""
    d = _direct_tool_route(_st("帮我对比一下培养方案，我该选哪些课？"))
    assert d["tool_calls"][0]["args"]["major"] == "计算机科学与技术"


def test_other_college_without_compare_keyword_keeps_own_major(colleges):
    """没提对比/转专业时，即使点了别的学院也不改变既有行为。"""
    d = _direct_tool_route(_st("我的培养方案完成得怎么样了"))
    assert d["tool_calls"][0]["args"]["major"] == "计算机科学与技术"


def test_cross_major_result_short_circuits_to_compose(colleges):
    """目标专业进度已有结果时不再重复调用。"""
    q = "我想转去物理学院，对比一下物理学院的培养方案"
    d = _direct_tool_route(_st(q, done=("get_program_progress",)))
    assert d["decision"] == "compose"


def test_personal_tree_only_injected_for_own_major(monkeypatch):
    """个人方案树只代表本人专业；问别人学院时注入会顶掉目标专业。

    `program_tools._resolve_courses` 里 personal_tree 无条件优先于 major，
    所以必须在注入侧挡住，否则真实 CAS 用户的跨专业对比会拿到自己的方案。
    """
    import agents.qa.nodes as nodes

    monkeypatch.setattr(nodes, "_load_personal_tree", lambda sid=None: {"type": {"nameZh": "个人"}})
    monkeypatch.setattr(nodes, "_load_taken_courses", lambda sid: ["热学B"])
    state = {"user_profile": {"major": "计算机科学与技术", "grade": "2025级"}}

    mine = {"major": "计算机科学与技术", "grade": "2025级"}
    nodes._enrich_program_args(mine, state, "PB25111691", include_taken=True)
    assert mine.get("personal_tree") is not None, "本人专业应当注入个人方案树"

    other = {"major": "物理学院", "grade": "2025级"}
    nodes._enrich_program_args(other, state, "PB25111691", include_taken=True)
    assert "personal_tree" not in other, "目标专业不得注入个人方案树"
    assert other.get("taken_courses") == ["热学B"], "已修课程仍须注入（对比需要）"


# ── 方案定位：一个学院下并列多个专业，不得落到任意一个 ──

def _resolver():
    import sqlite3

    from tools._program_resolve import resolve_program

    conn = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn, resolve_program


@pytest.mark.skipif(not _DB.exists(), reason="需要 data/course_data.db（部署数据，不入 git）")
@pytest.mark.parametrize("query,expect", [
    ("物理学院", "物理学专业培养方案"),
    # 带数字前缀的原始 college 值也必须正确——调用方（LLM/advisor_tools）可能原样传入
    ("203物理学院", "物理学专业培养方案"),
    ("数学科学学院", "数学与应用数学专业培养方案"),
    ("001数学科学学院", "数学与应用数学专业培养方案"),
    ("计算机科学与技术学院", "计算机科学与技术专业培养方案"),
    ("215计算机科学与技术学院", "计算机科学与技术专业培养方案"),
])
def test_college_query_picks_the_matching_major(query, expect):
    """回归：`物理学院` 曾命中「天文学专业培养方案」——同级同优先级落到任意一个。

    修法：并列时按「方案名与学院词干的公共前缀长度」排序，不依赖具体学院命名。
    """
    conn, resolve_program = _resolver()
    try:
        got = resolve_program(conn, query, "2025级")
    finally:
        conn.close()
    assert got is not None, f"{query} 未定位到方案"
    assert got["name"] == expect, f"{query} 定位到 {got['name']!r}，期望 {expect!r}"


@pytest.mark.skipif(not _DB.exists(), reason="需要 data/course_data.db（部署数据，不入 git）")
@pytest.mark.parametrize("grade", ["2025级", "2026级"])
def test_major_query_prefers_prefix_match_over_longer_sibling(grade):
    """`物理学` 不应落到「应用物理学专业培养方案」。"""
    conn, resolve_program = _resolver()
    try:
        got = resolve_program(conn, "物理学", grade)
    finally:
        conn.close()
    assert got is not None
    assert got["name"] == "物理学专业培养方案", f"{grade} 定位到 {got['name']!r}"
