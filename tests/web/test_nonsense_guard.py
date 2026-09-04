"""天马行空输入防护：无命中检索结果不进降级输出 / LLM 收到显式未命中提示。"""

from __future__ import annotations

from agents.qa.nodes import (
    _build_tool_summary,
    _fallback_answer,
    _is_missed_search,
    _llm_down_answer,
)


def _missed_search() -> dict:
    return {
        "tool": "search_faq",
        "status": "done",
        "result": {
            "found": False,
            "results": [
                {"content": "化学与材料科学学院 朱芸 0551-63601696", "score": 0.28, "source": "通讯录"},
                {"content": "黑白A4打印 0.1元/面", "score": 0.27, "source": "打印指南"},
            ],
            "top_score": 0.28,
        },
    }


def _hit_search() -> dict:
    return {
        "tool": "search_faq",
        "status": "done",
        "result": {
            "found": True,
            "results": [{"content": "学生证补办流程：先到教务处网站填写申请……", "score": 0.78, "source": "教务处"}],
            "top_score": 0.78,
        },
    }


def test_missed_search_detected() -> None:
    assert _is_missed_search(_missed_search())
    assert not _is_missed_search(_hit_search())
    assert not _is_missed_search({"tool": "query_schedule", "result": {"courses": []}})


def test_llm_down_answer_drops_missed_search() -> None:
    answer = _llm_down_answer(
        _build_tool_summary([_missed_search()]),
        "",
        [_missed_search()],
        [],
    )
    assert "朱芸" not in answer
    assert "打印" not in answer


def test_llm_down_answer_keeps_hit_search() -> None:
    summary = _build_tool_summary([_hit_search()])
    answer = _llm_down_answer(summary, "", [_hit_search()], [])
    assert "学生证补办流程" in answer


def test_tool_summary_labels_missed_search() -> None:
    summary = _build_tool_summary([_missed_search()])
    assert "未找到与问题相关的知识" in summary
    assert "朱芸" not in summary


def test_fallback_answer_drops_missed_search() -> None:
    answer = _fallback_answer([_missed_search(), {"tool": "x", "status": "done", "result": {}}], [])
    assert "朱芸" not in answer
