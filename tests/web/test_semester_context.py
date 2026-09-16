"""学期上下文注入 + 转专业政策的检索保障（2026-09-16）。

背景：管线原先只注入「日期 + 星期」，模型知道今天几号却不知道这是哪个学期、
第几教学周——于是"下个学期应该选什么课"会把**当前学期当成下学期**（实测把
2秋 的课答成"下个学期"），政策里的"第14~16教学周"也无法换算成日期。
本文件锁住：学期上下文能被注入、学年序号推算正确、转专业问句会补检索政策。
"""
from __future__ import annotations

import pytest

from agents.qa.nodes import (
    _POLICY_RETRIEVAL_KW,
    _POLICY_RETRIEVAL_QUERY,
    _next_term_year_index,
    _plan_semester_call,
)

_ORIGINAL_Q = ("我是计算机系的，大三想转去物理学院，帮我对比下我目前选的课和物理学院的培养方案，"
               "我下个学期应该选哪些课来拉近差距和过度呢？")


# ── 学期上下文 ────────────────────────────────────────────

def test_semester_context_has_term_and_teaching_week():
    """注入文本必须含学期标签与教学周，否则「下个学期」无从推算。"""
    from utils.semester_time import semester_context_text

    text = semester_context_text()
    assert "当前学期" in text
    assert "教学周" in text
    assert "第1周" in text, "必须给出教学周→日期对照，避免模型自己做日期加法"


def test_semester_label_prefers_config():
    from utils.semester_time import semester_label_text

    label = semester_label_text()
    assert label, "学期配置可用时标签不应为空"
    assert "（" in label, "标签应带学年学期，如 2026-2027-1（2026秋）"


# ── 学年序号推算 ──────────────────────────────────────────

@pytest.mark.parametrize(
    "semester_name,grade,expected",
    [
        ("2026-2027-1", "2025级", 2),   # 2秋 → 下个学期 2春，仍属第 2 学年
        ("2026-2027-2", "2025级", 3),   # 2春 → 下个学期 3秋，进入第 3 学年
        ("2026-2027-1", "2024级", 3),   # 3秋 → 下个学期 3春
    ],
)
def test_next_term_year_index(monkeypatch, semester_name, grade, expected):
    import config

    monkeypatch.setattr(
        config, "SEMESTER",
        {"name": semester_name, "start_date": "2026-08-31", "total_weeks": 18},
        raising=False,
    )
    assert _next_term_year_index(grade) == expected


def test_next_term_year_index_without_grade_is_none():
    """年级缺失时不做学年规划（退回原行为，而不是瞎猜）。"""
    assert _next_term_year_index("") is None


# ── 排课调用 ──────────────────────────────────────────────

def test_plan_semester_only_for_next_term_questions():
    assert _plan_semester_call(_ORIGINAL_Q, "物理学院", "2025级") is not None
    assert _plan_semester_call("三年级能转去物理学院吗", "物理学院", "2025级") is None


def test_plan_semester_targets_asked_major():
    """排课必须查被问到的专业，不得悄悄换成本人专业。"""
    call = _plan_semester_call(_ORIGINAL_Q, "物理学院", "2025级")
    assert call["tool"] == "plan_semester"
    assert call["args"]["major"] == "物理学院"
    assert isinstance(call["args"]["year_index"], int)


# ── 政策检索保障 ──────────────────────────────────────────

def test_policy_retrieval_covers_object_verb_phrasing():
    """「转去/转到/转入」这类动宾式说法必须触发补检索——原题就是「转去物理学院」。"""
    assert any(k in _ORIGINAL_Q for k in _POLICY_RETRIEVAL_KW), \
        "原题未触发政策补检索，转专业窗口/资格就拿不到"
    assert "转去" in _POLICY_RETRIEVAL_KW


def test_policy_query_mentions_week_and_eligibility():
    """补检索口径要覆盖政策文档的关键词（窗口周次 + 年级资格）。"""
    assert "教学周" in _POLICY_RETRIEVAL_QUERY
    assert "二年级" in _POLICY_RETRIEVAL_QUERY and "三年级" in _POLICY_RETRIEVAL_QUERY
