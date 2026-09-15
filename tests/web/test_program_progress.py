# -*- coding: utf-8 -*-
"""`get_program_progress` 的「已修记录是否已知」契约回归。

背景（2026-09-15）：培养方案路由修好之后，回答已能报出
「必修已修 23/47 门、学分 56.5/126.0，完成度约 44.8%」，
但正文紧接着一句「也未核验你的已修记录」——自相矛盾，会让演示评审
以为这个 44.8% 是编的。

根因：`get_program_progress` 的返回值里没有任何字段说明「已修课程到底传没传」。
`taken_courses=None` 时它返回 `required_taken=0, percent=0.0`，上层无法区分
「学生真的没修」和「我们不知道他修了什么」，只能一律加免责声明；反过来，
一个成绩表全没匹配上的学生会被当成「完成度 0%」这个事实讲出来。

`recommend_courses` 早已用 `_taken_known`（`tools/advisor_tools.py:1412`）解决同一问题，
本文件把 `get_program_progress` 拉到同一口径：
- 工具透出 `taken_courses_known`
- 摘要如实交代判定依据（已知）或明确警示占位（未知）
- 数据卡标题在未知时不得叫「缺口」
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.qa.nodes import _STRUCTURE_SPECS, _build_tool_summary, _tool_to_structured

_DB = Path(__file__).resolve().parents[2] / "data" / "course_data.db"


def _progress_result(**over) -> dict:
    base = {
        "program_id": 3011,
        "name": "计算机科学与技术专业培养方案",
        "required_total": 47,
        "required_taken": 23,
        "required_remaining": [{"code": "CS1002A", "name": "计算系统概论A",
                                "credit": 4.0, "term": "2秋", "category": "专业基础课程"}],
        "credits_taken": 56.5,
        "credits_required": 126.0,
        "percent": 44.8,
        "taken_courses_known": True,
        "modules_progress": [{"category": "专业基础课程", "taken": 2, "total": 7}],
        "source": "generic",
    }
    base.update(over)
    return base


def _summary(**over) -> str:
    return _build_tool_summary([
        {"tool": "get_program_progress", "status": "done", "result": _progress_result(**over)},
    ])


def _card(**over):
    tables = _tool_to_structured([
        {"tool": "get_program_progress", "status": "done", "result": _progress_result(**over)},
    ])
    assert tables, "卡片未生成"
    return tables[0]


# ── 工具返回值契约（需要真实评课库；tests/web 其余用例不依赖它，故缺失时跳过）──

@pytest.mark.skipif(not _DB.exists(), reason="需要 data/course_data.db（部署数据，不入 git）")
def test_tool_reports_taken_courses_known_true():
    from tools.program_tools import get_program_progress

    res = get_program_progress.invoke({
        "major": "计算机科学与技术", "grade": "2025级",
        "taken_courses": ["热学B", "散打I"],
    })
    assert res["taken_courses_known"] is True


@pytest.mark.skipif(not _DB.exists(), reason="需要 data/course_data.db（部署数据，不入 git）")
def test_tool_reports_unknown_and_zero_placeholder_without_records():
    """未传已修课程时必须自曝未知，不能让人把 0 当成事实。"""
    from tools.program_tools import get_program_progress

    res = get_program_progress.invoke({"major": "计算机科学与技术", "grade": "2025级"})
    assert res["taken_courses_known"] is False
    assert res["required_taken"] == 0
    assert res["percent"] == 0.0


@pytest.mark.skipif(not _DB.exists(), reason="需要 data/course_data.db（部署数据，不入 git）")
def test_tool_exposes_matched_required_list():
    """跨专业对比要回答「我的哪些课对应上了目标专业」，工具必须透出匹配清单。"""
    from tools.program_tools import get_program_progress

    res = get_program_progress.invoke({
        "major": "计算机科学与技术", "grade": "2025级",
        "taken_courses": ["数学分析(B1)", "线性代数(B1)", "热学B", "散打I"],
    })
    matched = res.get("required_taken_list") or []
    assert res["required_taken"] == len(matched)
    assert matched, "至少应有课程匹配上方案必修"
    assert all("name" in c and "credit" in c for c in matched)


# ── 摘要措辞 ──

def test_summary_states_basis_when_records_known():
    text = _summary()
    assert "已修判定依据" in text
    assert "必修缺口" in text
    assert "缺省占位" not in text


def test_summary_warns_placeholder_when_records_unknown():
    text = _summary(taken_courses_known=False, required_taken=0, percent=0.0)
    assert "缺省占位" in text
    assert "必修缺口" not in text
    assert "必修待确认" in text


# ── 数据卡标题 ──

def test_card_title_calls_it_gap_only_when_records_known():
    assert _card()["title"] == "培养方案缺口"
    assert _card(taken_courses_known=False)["title"] == "培养方案必修课程（已修记录未知）"


def test_static_string_titles_still_supported():
    """动态标题是新增的可选能力，既有静态标题不得回归。"""
    assert not callable(_STRUCTURE_SPECS["get_my_program"]["title"])
    tables = _tool_to_structured([{
        "tool": "get_my_program", "status": "done",
        "result": {"name": "方案", "courses": [
            {"code": "C1", "name": "课", "credit": 1, "required": "必修", "term": "1秋"},
        ]},
    }])
    assert tables and tables[0]["title"] == "培养方案课程清单"


def test_callable_title_failure_keeps_card_usable():
    """标题函数抛异常时整卡仍要可用（同「单行异常只跳该行」的既有约定）。"""
    import agents.qa.nodes as nodes

    spec = nodes._STRUCTURE_SPECS["get_program_progress"]
    original = spec["title"]
    spec["title"] = lambda payload: 1 / 0
    try:
        tables = nodes._tool_to_structured([
            {"tool": "get_program_progress", "status": "done", "result": _progress_result()},
        ])
    finally:
        spec["title"] = original
    assert tables and tables[0]["title"] == "数据卡"
    assert tables[0]["rows"], "标题降级不得丢掉行数据"
