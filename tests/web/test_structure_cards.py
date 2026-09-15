# -*- coding: utf-8 -*-
"""结构化数据卡规格回归（2026-09-15 新增 推荐/评课/对比 三类）。

背景：这三类此前**没有卡**，只能靠 LLM 用「名称|数值」纯文本竖线表达，
渲染出来是一行带竖线的普通文字。补卡后由前端直接渲染表格，不经 LLM 重述。
"""
from __future__ import annotations

import pytest

from agents.qa.nodes import _STRUCTURE_SPECS, _tool_to_structured


def _card(tool: str, payload: dict) -> list[dict]:
    return _tool_to_structured([{"tool": tool, "status": "done", "result": payload}])


def test_new_specs_are_registered():
    for tool in ("recommend_courses", "analyze_teacher", "compare_courses"):
        assert tool in _STRUCTURE_SPECS, f"{tool} 缺结构化卡规格"


def test_recommend_courses_card():
    payload = {"recommendations": [{
        "name": "组合数学", "teachers": [{"name": "邵帅"}], "credit": 3.0,
        "rating_avg": 7.6, "rate_count": 70,
        "program_hint": {"term": "2秋"}, "reasons": ["培养方案必修缺口"],
    }]}
    tables = _card("recommend_courses", payload)
    assert len(tables) == 1
    table = tables[0]
    assert table["title"] == "课程推荐"
    assert table["source_tool"] == "recommend_courses"
    assert table["rows"][0] == ["组合数学", "邵帅", "3.0", "2秋", "7.6分·70条", "培养方案必修缺口"]


def test_analyze_teacher_card_course_mode():
    """课程模式：teachers = 各班（含合教组合），带维度众数。"""
    payload = {"course": "组合数学", "teachers": [{
        "name": "许胤龙, 吕敏", "dept": "计算机科学与技术系",
        "rating_avg": 6.5, "rate_count": 47,
        "dims_mode": {"难度": "困难", "作业": "中等", "给分": "一般", "收获": "一般"},
    }]}
    (table,) = _card("analyze_teacher", payload)
    assert table["title"] == "评课对比"
    assert table["rows"][0] == ["许胤龙, 吕敏", "计算机科学与技术系", "6.5", "47", "困难", "中等", "一般", "一般"]


def test_analyze_teacher_card_teacher_mode():
    """教师模式：courses = 该教师各课；缺字段用占位符而不是抛错。"""
    payload = {"teacher": "邵帅", "courses": [{
        "name": "离散数学", "dept": "计算机科学与技术系",
        "rating_avg": 9.3, "rate_count": 28, "dims_mode": {"难度": "困难"},
    }]}
    (table,) = _card("analyze_teacher", payload)
    assert table["rows"][0][0] == "离散数学"
    assert table["rows"][0][4] == "困难"
    assert table["rows"][0][5] == ""  # 缺失维度留空，不抛错


def test_analyze_teacher_card_missing_name_falls_back():
    (table,) = _card("analyze_teacher", {"course": "X", "teachers": [{"rating_avg": 5.0}]})
    assert table["rows"][0][0] == "（未标注老师）"
    assert table["rows"][0][2] == "5.0"


def test_compare_courses_card():
    payload = {
        "course_a": {"name": "组合数学", "rating_avg": 7.6, "rate_count": 70,
                     "dims": {"avg": {"难度": 4.1, "作业": 4.7, "给分": 8.6, "收获": 7.0}}},
        "course_b": {"name": "社会心理学", "rating_avg": 9.0, "rate_count": 2,
                     "dims": {"avg": {"难度": 10.0, "作业": 9.0, "给分": 8.0, "收获": 7.0}}},
    }
    (table,) = _card("compare_courses", payload)
    assert table["title"] == "课程对比"
    assert [row[0] for row in table["rows"]] == ["组合数学", "社会心理学"]
    assert table["rows"][0][1:3] == ["7.6", "70"]


def test_row_error_skips_only_that_row(monkeypatch):
    """回归：单行异常只跳过该行，**不得**连累工具状态（act 里抛错会把工具标成失败）。"""
    monkeypatch.setitem(_STRUCTURE_SPECS, "query_grade", {
        "title": "测试卡", "items_key": "grades", "columns": ["值"],
        "row": lambda r: [r["must_exist"]],
    })
    tables = _card("query_grade", {"grades": [{"must_exist": "ok"}, {"other": 1}]})
    assert len(tables) == 1
    assert tables[0]["rows"] == [["ok"]]


def test_items_fn_error_does_not_raise(monkeypatch):
    monkeypatch.setitem(_STRUCTURE_SPECS, "query_grade", {
        "title": "测试卡", "columns": ["值"],
        "items": lambda payload: payload["missing_key"],   # 故意抛错
        "row": lambda r: [str(r)],
    })
    assert _card("query_grade", {"grades": []}) == []


def test_unknown_tool_and_error_status_produce_no_card():
    assert _card("not_a_tool", {"x": []}) == []
    assert _tool_to_structured([{"tool": "query_grade", "status": "error", "result": {}}]) == []


@pytest.mark.parametrize("tool", ["recommend_courses", "analyze_teacher", "compare_courses"])
def test_new_specs_have_required_keys(tool):
    spec = _STRUCTURE_SPECS[tool]
    assert spec["title"] and spec["columns"] and spec["row"]
    assert ("items" in spec) or ("items_key" in spec)
