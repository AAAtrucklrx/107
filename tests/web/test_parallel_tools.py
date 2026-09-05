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


def test_tool_to_structured_builds_tables() -> None:
    """工具结果 → 结构化卡片：成绩/课表映射正确；空结果/未知工具不输出。"""
    results = [
        {"tool": "query_grade", "status": "done", "result": {
            "grades": [{"semester": "2025-1", "course_name": "数学分析(B1)", "credits": 6.0,
                        "score": 85, "score_text": None, "grade_point": 3.7}],
            "count": 1,
        }},
        {"tool": "query_daily_schedule", "status": "done", "result": {
            "courses": [{"periods": "1-2节", "course_name": "英语", "location": "东区一教", "teacher": "王老师"}],
        }},
        {"tool": "query_exam", "status": "done", "result": {"exams": []}},
        {"tool": "unknown_tool", "status": "done", "result": {"x": 1}},
        {"tool": "query_grade", "status": "error", "result": {"error": "x"}},
    ]
    tables = nodes._tool_to_structured(results)
    assert len(tables) == 2
    grade = tables[0]
    assert grade["title"] == "个人成绩单"
    assert grade["columns"] == ["学期", "课程", "学分", "成绩", "绩点"]
    assert grade["rows"][0] == ["2025-1", "数学分析(B1)", "6.0", "85", "3.7"]
    schedule = tables[1]
    assert schedule["rows"][0][1] == "英语"


def test_tool_to_structured_handles_score_text() -> None:
    """等级制成绩（score=-1 + score_text）显示原文。"""
    tables = nodes._tool_to_structured([{
        "tool": "query_grade", "status": "done",
        "result": {"grades": [{"semester": "2025-2", "course_name": "政治", "credits": 2,
                               "score": -1, "score_text": "通过", "grade_point": 3.0}]},
    }])
    assert tables[0]["rows"][0][3] == "通过"


def test_structured_extension_specs() -> None:
    """批量扩展：活动/空教室/培养方案/周视图/课程目录均产出卡片。"""
    cases = {
        "query_activities": {"activities": [{"name": "机器人讲座", "organizer": "校团委",
                                              "start": "2026-09-10 14:00", "end": "2026-09-10 16:00",
                                              "place": "西区3B101", "apply_end": "2026-09-09"}]},
        "find_empty_room": {"empty_rooms": [{"room": "东区一教101", "free_slots": "08:00-10:00"}]},
        "get_my_program": {"courses": [{"code": "MATH1001", "name": "数学分析", "required": True,
                                        "credit": 6, "term": "1秋"}]},
        "get_program_progress": {"required_remaining": [{"code": "PHYS2001", "name": "力学", "credit": 4, "term": "2秋"}]},
        "plan_semester": {"terms": [{"term": "2秋", "courses": [{"name": "线性代数", "credit": 4}]}]},
        "get_day_view": {"events": [{"title": "组会", "start_time": "14:00", "end_time": "16:00", "location": "E2110"}]},
        "get_week_view": {"daily": {"周一": [{"title": "体育", "start_time": "08:00", "end_time": "09:40", "location": "操场"}]}},
        "search_courses": {"courses": [{"course_code": "CS1001", "course_name": "程序设计", "dept": "计算机"}]},
    }
    for tool, payload in cases.items():
        tables = nodes._tool_to_structured([{"tool": tool, "status": "done", "result": payload}])
        assert tables, f"{tool} 未产出卡片"
        assert tables[0]["title"] and tables[0]["columns"] and tables[0]["rows"]
    # plan_semester 嵌套拍平验证
    tables = nodes._tool_to_structured([{"tool": "plan_semester", "status": "done",
                                         "result": {"terms": [{"term": "2秋", "courses": [{"name": "线性代数", "credit": 4}]}]}}])
    assert tables[0]["rows"][0] == ["2秋", "线性代数", "4"]


def test_create_llm_disables_thinking() -> None:
    """P1②：create_llm 通过 extra_body 关闭官网推理（省 0.5-1s/次）。"""
    from utils.llm_client import create_llm
    llm = create_llm(model="deepseek-v4-flash", temperature=0)
    extra = (llm.model_kwargs or {}).get("extra_body") or {}
    assert extra.get("thinking", {}).get("type") == "disabled"
