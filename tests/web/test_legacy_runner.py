"""Legacy QA adapter exposes real local evidence instead of a blanket source."""

from __future__ import annotations

import asyncio

from xiaowo_web.auth.models import Principal
from xiaowo_web.chat.models import QaRunRequest
from xiaowo_web.chat.runner import LegacyQaRunner


def _request() -> QaRunRequest:
    return QaRunRequest(
        run_id="run-local",
        question="学生证丢了怎么办？",
        requested_mode="local",
        effective_mode="local",
        principal=Principal("", "anonymous", {}, False, "session-local"),
        conversation_id=None,
    )


def test_real_candidates_are_deduplicated_and_linked_to_the_claim() -> None:
    def fake_run_qa(_question: str, **_kwargs) -> dict:
        return {
            "answer": "学生证遗失后应按教务处流程申请补办。",
            "intent": "知识问答",
            "candidates_found": True,
            "candidates": [
                {
                    "id": "student-card-1",
                    "title": "学生证补办",
                    "source": "教务处：https://www.teach.ustc.edu.cn/service/card",
                    "content": "学生证遗失后，应提交补办申请。",
                    "score": 0.83,
                    "is_official": True,
                    "last_updated": "2026-08-01",
                },
                {
                    "id": "student-card-2",
                    "title": "学生证补办注意事项",
                    "source": "教务处：https://www.teach.ustc.edu.cn/service/card",
                    "content": "领取时间以教务处通知为准。",
                    "score": 0.71,
                    "is_official": True,
                },
            ],
            "tool_results": [],
            "error": "",
        }

    runner = LegacyQaRunner(fake_run_qa)
    try:
        answer = asyncio.run(runner.run(_request()))
    finally:
        runner.close()

    assert len(answer.sources) == 1
    source = answer.sources[0]
    assert source["source_id"] != "local-xiaowo"
    assert source["title"] == "学生证补办"
    assert source["display_url"] == "https://www.teach.ustc.edu.cn/service/card"
    assert source["institution"] == "中国科学技术大学教务处"
    assert source["level"] == "official_primary"
    assert source["relevance_score"] == 0.83
    assert answer.claims[0]["status"] == "confirmed"
    assert answer.claims[0]["evidence"][0]["source_id"] == source["source_id"]
    assert answer.claims[0]["evidence"][0]["excerpt_hash"] != "legacy-adapter"
    assert "[1]" in answer.markdown


def test_empty_done_tool_does_not_confirm_an_answer() -> None:
    def fake_run_qa(_question: str, **_kwargs) -> dict:
        return {
            "answer": "当前没有查到可用记录。",
            "intent": "知识问答",
            "candidates_found": False,
            "candidates": [],
            "tool_results": [{
                "tool": "query_exam",
                "status": "done",
                "result": {"exams": [], "count": 0, "source": "fallback"},
            }],
            "error": "",
        }

    runner = LegacyQaRunner(fake_run_qa)
    try:
        answer = asyncio.run(runner.run(_request()))
    finally:
        runner.close()

    assert answer.claims[0]["status"] == "insufficient"
    assert answer.claims[0]["evidence"] == []
    assert answer.sources == []


def test_tool_result_uses_its_official_link_and_hashed_relation() -> None:
    def fake_run_qa(_question: str, **_kwargs) -> dict:
        return {
            "answer": "综合教务系统可办理相关事项。",
            "intent": "校园链接",
            "candidates_found": False,
            "candidates": [],
            "tool_results": [{
                "tool": "render_link",
                "status": "done",
                "result": {
                    "found": True,
                    "name": "综合教务系统",
                    "url": "https://jw.ustc.edu.cn/",
                    "description": "选课与教务办理入口",
                },
            }],
            "error": "",
        }

    runner = LegacyQaRunner(fake_run_qa)
    try:
        answer = asyncio.run(runner.run(_request()))
    finally:
        runner.close()

    assert answer.claims[0]["status"] == "confirmed"
    assert answer.sources[0]["title"] == "综合教务系统"
    assert answer.sources[0]["display_url"] == "https://jw.ustc.edu.cn/"
    assert answer.sources[0]["level"] == "tool_result"
    assert answer.claims[0]["evidence"][0]["evidence_type"] == "tool"
    assert len(answer.claims[0]["evidence"][0]["excerpt_hash"]) == 64
