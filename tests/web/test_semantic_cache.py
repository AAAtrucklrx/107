"""语义缓存：命中/写入/命名空间隔离/TTL/哈希定向失效 + runner 接入。"""

from __future__ import annotations

import hashlib
import json

from xiaowo_web.knowledge.semantic_cache import SemanticCache


def _fake_embedder(dim: int = 8):
    """确定性伪 embedder：按关键词映射到固定方向，便于构造相似/无关向量。"""

    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for keyword, idx in {
            "学生证": 0, "补办": 1, "证件": 0,
            "选课": 2, "退课": 3,
            "校车": 4, "班车": 4,
        }.items():
            if keyword in text:
                vec[idx] = 1.0
        if not any(v > 0 for v in vec):
            vec[dim - 1] = 1.0  # 无关文本落到独立方向
        return vec

    return embed


def _digest(content: str) -> str:
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_store_and_semantic_hit(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.5)
    assert cache.store("学生证丢了怎么补办", "补办流程：……", "production",
                       source_hashes=[_digest("chunk-a")]) is True
    hit = cache.lookup("学生证怎么补办？", "production")
    assert hit is not None
    assert "补办流程" in hit["answer"]
    assert hit["score"] >= 0.5


def test_unrelated_query_misses(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.5)
    cache.store("学生证丢了怎么补办", "补办流程：……", "production")
    assert cache.lookup("校车班车路线有哪些", "production") is None


def test_namespace_isolation(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.5)
    cache.store("学生证补办", "production 答案", "production")
    cache.store("学生证补办", "demo 答案", "demo")
    assert "production 答案" in cache.lookup("学生证补办", "production")["answer"]
    assert "demo 答案" in cache.lookup("学生证补办", "demo")["answer"]


def test_ttl_expiry(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.5, ttl_seconds=100)
    cache.store("学生证补办", "旧答案", "production", now=1000.0)
    assert cache.lookup("学生证补办", "production", now=1050.0) is not None
    assert cache.lookup("学生证补办", "production", now=2000.0) is None  # 过期


def test_invalidate_missing_is_targeted(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.5)
    h_a, h_b = _digest("chunk-a"), _digest("chunk-b")
    cache.store("学生证补办", "答案A（依据 chunk-a）", "production", source_hashes=[h_a])
    cache.store("选课时间安排", "答案B（依据 chunk-b）", "production", source_hashes=[h_b])

    # 新发布只包含 chunk-b：答案A 依据缺失 → 失效；答案B 保留
    removed = cache.invalidate_missing({h_b}, namespace="production")
    assert removed == 1
    assert cache.lookup("选课时间安排", "production") is not None
    assert cache.lookup("学生证补办", "production") is None


def test_invalidate_ignores_entries_without_hashes(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.5)
    cache.store("学生证补办", "无来源答案", "production")  # source_hashes 为空 → 不参与失效
    assert cache.invalidate_missing({"unrelated-hash"}, namespace="production") == 0
    assert cache.lookup("学生证补办", "production") is not None


def test_runner_writes_and_hits_cache(tmp_path) -> None:
    """集成：QA 成功回答写入缓存，同义追问命中缓存直接返回。"""
    from xiaowo_web.chat.runner import LegacyQaRunner
    from xiaowo_web.knowledge.semantic_cache import SemanticCache

    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.6)
    calls = {"n": 0}

    def fake_run_qa(question, **kwargs):
        calls["n"] += 1
        return {
            "answer": "学生证补办流程：先到教务处网站填写申请，再到现场办理。",
            "intent": "知识问答",
            "candidates": [{"content": "学生证补办：教务处网站申请 → 现场办理", "source": "教务处"}],
            "candidates_found": True,
            "tool_results": [
                {"tool": "search_faq", "status": "done",
                 "result": {"found": True, "results": [{"content": "学生证补办流程", "source": "教务处"}]}},
            ],
        }

    import asyncio
    from types import SimpleNamespace

    principal = SimpleNamespace(
        auth_mode="anonymous", is_authenticated=False,
        principal_id=None, profile={}, session_key="s1",
        history_owner_key="s1", review_namespace=None,
    )
    request = SimpleNamespace(
        run_id="r1", question="学生证丢了怎么补办", requested_mode="auto",
        effective_mode="auto", principal=principal, conversation_id=None,
        chat_history=[], emit_stage=None,
    )
    runner = LegacyQaRunner(
        run_qa_func=fake_run_qa, approved_retriever=None, semantic_cache=cache,
    )
    first = asyncio.run(runner.run(request))
    assert calls["n"] == 1
    assert "补办" in first.markdown
    second = asyncio.run(runner.run(request))
    assert calls["n"] == 1  # 未再跑全链路
    assert "补办" in second.markdown
    assert second.terminal_reason == "cache_hit"


def test_runner_skips_cache_for_personal_tools(tmp_path) -> None:
    """个人数据工具参与的回答不写缓存——下一问不会被污染。"""
    from xiaowo_web.chat.runner import LegacyQaRunner
    from xiaowo_web.knowledge.semantic_cache import SemanticCache

    cache = SemanticCache(tmp_path / "cache.db", embedder=_fake_embedder(), threshold=0.6)

    def fake_run_qa(question, **kwargs):
        return {
            "answer": "你本学期的课表如下……",
            "intent": "课表查询",
            "candidates": [{"content": "个人课表内容", "source": "教务"}],
            "candidates_found": True,
            "tool_results": [
                {"tool": "query_schedule", "status": "done",
                 "result": {"found": True, "courses": [{"course_name": "数学分析"}]}},
            ],
        }

    import asyncio
    from types import SimpleNamespace
    import xiaowo_web.chat.runner as runner_mod

    principal = SimpleNamespace(
        auth_mode="anonymous", is_authenticated=False,
        principal_id=None, profile={}, session_key="s2",
        history_owner_key="s2", review_namespace=None,
    )
    request = SimpleNamespace(
        run_id="r2", question="我的课表", requested_mode="auto",
        effective_mode="auto", principal=principal, conversation_id=None,
        chat_history=[], emit_stage=None,
    )
    runner = LegacyQaRunner(
        run_qa_func=fake_run_qa, approved_retriever=None, semantic_cache=cache,
    )
    result = asyncio.run(runner.run(request))
    assert "课表" in result.markdown
    assert cache.stats()["entries"] == 0  # 个人化回答未入缓存


def calls_personal(result) -> bool:
    return "课表" in result.markdown
