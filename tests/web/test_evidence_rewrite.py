"""Query rewriting unit tests for the web evidence pipeline."""

from __future__ import annotations

import asyncio

from xiaowo_web.evidence.rewrite import QueryRewriter, _RewritePayload


def _run(coro):
    return asyncio.run(coro)


def test_short_question_skips_rewrite() -> None:
    rewriter = QueryRewriter(lambda _prompt: _RewritePayload(queries=["改写词"]))
    assert rewriter.wants_rewrite("学生证丢了") is False
    assert _run(rewriter.rewrite("学生证丢了")) is None


def test_long_question_returns_keyword_queries() -> None:
    def invoke(_prompt: str) -> dict:
        return {"queries": ["科大 教务处 选课通知", "2026 秋季本科生选课安排"]}

    rewriter = QueryRewriter(invoke)
    result = _run(rewriter.rewrite("中国科学技术大学2026年秋季学期本科生选课通知的最新安排是什么？"))
    assert result == ["科大 教务处 选课通知", "2026 秋季本科生选课安排"]


def test_duplicate_or_original_queries_are_dropped() -> None:
    long_question = "这条问题很长用来触发改写流程并且验证去重与剔除原句的逻辑是否正确执行。"
    def invoke(_prompt: str) -> dict:
        return {"queries": [long_question, "科大选课通知", "科大选课通知"]}

    rewriter = QueryRewriter(invoke)
    result = _run(rewriter.rewrite(long_question))
    assert result == ["科大选课通知"]


def test_invalid_payload_falls_back_to_none() -> None:
    rewriter = QueryRewriter(lambda _prompt: {"bad": "shape"})
    assert _run(rewriter.rewrite("这条问题非常长，用来触发改写失败时的兜底路径而不至于让管线崩溃。")) is None
    assert rewriter.last_error is not None


def test_prompt_includes_short_hint_when_requested() -> None:
    seen: list[str] = []

    def invoke(prompt: str) -> dict:
        seen.append(prompt)
        return {"queries": ["更短关键词"]}

    rewriter = QueryRewriter(invoke)
    result = _run(rewriter.rewrite("这条问题同样非常长，用来检验简短改写提示词是否真正被拼接进了提示里。", short_hint=True))
    assert result == ["更短关键词"]
    assert "更简短" in seen[0]
