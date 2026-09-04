"""并行工具调用：计划构建 + act 并行执行 + ContextVar 隔离。"""

from __future__ import annotations

import asyncio
import time

import agents.qa.nodes as nodes
from agents.qa.nodes import _build_tool_plan, act


def test_build_tool_plan_prefers_tools_array() -> None:
    data = {"tools": [{"tool": "query_grade", "args": {}}, {"tool": "query_schedule", "args": {}}]}
    plan, note = _build_tool_plan(data, set())
    assert [c["tool"] for c in plan] == ["query_grade", "query_schedule"]
    assert note == ""


def test_build_tool_plan_falls_back_to_single_tool() -> None:
    plan, _ = _build_tool_plan({"tool": "query_exam", "args": {"year": 2026}}, set())
    assert [c["tool"] for c in plan] == ["query_exam"]


def test_build_tool_plan_dedupes_and_removes_unknown_done() -> None:
    data = {"tools": [
        {"tool": "query_grade"}, {"tool": "query_grade"},
        {"tool": "not_a_real_tool"}, {"tool": "query_exam"},
    ]}
    plan, note = _build_tool_plan(data, {"query_exam"})
    assert [c["tool"] for c in plan] == ["query_grade"]
    assert "重复" in note and "未知工具" in note and "已有结果" in note


def test_build_tool_plan_empty_when_all_filtered() -> None:
    plan, note = _build_tool_plan({"tool": "already_done"}, {"already_done"})
    assert plan == [] and "禁止重复调用" in note


def test_act_runs_tools_in_parallel(monkeypatch) -> None:
    """两个 0.3s 工具并行执行：总耗时 < 串行 0.6s，结果按计划顺序回填。"""
    durations: dict[str, float] = {}

    def fake_tool_a(args):
        time.sleep(0.3)
        durations["a"] = time.time()
        return {"data": "A"}

    def fake_tool_b(args):
        time.sleep(0.3)
        durations["b"] = time.time()
        return {"data": "B"}

    class FakeFunc:
        def __init__(self, fn):
            self._fn = fn

        def invoke(self, args):
            return self._fn(args)

    registry = {"echo_a": FakeFunc(fake_tool_a), "echo_b": FakeFunc(fake_tool_b)}
    monkeypatch.setattr(nodes, "_build_tool_registry", lambda: registry)

    t0 = time.time()
    out = act({
        "decision": "call_tool",
        "tool_calls": [{"tool": "echo_a", "args": {}}, {"tool": "echo_b", "args": {}}],
        "tool_results": [],
        "rounds": 0,
        "student_id": "",
    })
    elapsed = time.time() - t0
    assert elapsed < 0.55, f"并行未生效: {elapsed:.2f}s"
    assert [r["tool"] for r in out["tool_results"]] == ["echo_a", "echo_b"]
    assert [r["status"] for r in out["tool_results"]] == ["done", "done"]
    assert out["rounds"] == 1
    assert out["tool_calls"] == []


def test_act_one_failure_does_not_affect_others(monkeypatch) -> None:
    def boom(_args):
        raise ValueError("boom")

    class FakeFunc:
        def __init__(self, fn):
            self._fn = fn

        def invoke(self, args):
            return self._fn(args)

    registry = {"echo_ok": FakeFunc(lambda a: {"ok": 1}), "echo_bad": FakeFunc(boom)}
    monkeypatch.setattr(nodes, "_build_tool_registry", lambda: registry)
    out = act({
        "decision": "call_tool",
        "tool_calls": [{"tool": "echo_bad", "args": {}}, {"tool": "echo_ok", "args": {}}],
        "tool_results": [], "rounds": 0, "student_id": "",
    })
    statuses = {r["tool"]: r["status"] for r in out["tool_results"]}
    assert statuses["echo_bad"] == "error"
    assert statuses["echo_ok"] == "done"


def test_tools_for_intent_trims_by_bucket() -> None:
    """意图裁剪：个人桶只含个人工具；知识问答只给万金油；跨桶并集。"""
    personal = nodes._tools_for_intent("查成绩")
    assert "query_grade" in personal and "query_schedule" in personal
    assert "recommend_courses" not in personal
    assert "search_faq" in personal  # 万金油保留

    course = nodes._tools_for_intent("选课推荐")
    assert "recommend_courses" in course
    assert "query_grade" not in course

    general = nodes._tools_for_intent("知识问答")
    assert "query_grade" not in general
    assert "recommend_courses" not in general
    assert "search_faq" in general

    cross = nodes._tools_for_intent("查课表", [
        {"intent": "查成绩", "score": 0.9}, {"intent": "查课表", "score": 0.8},
        {"intent": "课程搜索", "score": 0.4},
    ])
    assert "query_schedule" in cross and "query_grade" in cross
    # 第三不同桶不再并入（限制 2 桶）
    assert "search_courses" not in cross


def test_tool_catalog_covers_full_list() -> None:
    """"裁剪后仍可从目录重组完整清单（无工具丢失）。"""
    full = nodes._tool_list_full()
    for entry in nodes._TOOL_ENTRIES:
        name = entry.split("(")[0].strip()
        assert name in full
