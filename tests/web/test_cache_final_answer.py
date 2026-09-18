"""语义缓存只存**最终展示的答案**，且联网条目带护栏（2026-09-18 选项 2）。

背景（实测）：缓存原来只在本地 runner 内写，而外层 EvidenceAwareRunner 之后会用联网
答案覆盖它 → 缓存里存的是**用户没看到的草稿**：

- `中国科学技术大学2026年暑期社会实践立项通知`：缓存的是本地 353 字「暂时没有检索到
  对应的官方文件内容」，实际展示的是联网 1193 字；
- `中科大计算机学院教学秘书…院长`：缓存的是本地半成品（教秘有、院长「未收录」）。

改法：写入点交回外层 runner（最终 bundle 已定）+ `kind`(local/web) 分类型 TTL
（web 30 分钟 / local 24 小时）+ 连 limitations 一起存 + 命中时按 kind 决定 claim 状态。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

from tests.web.test_semantic_cache import _fake_embedder
from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.chat.runner import LegacyQaRunner
from xiaowo_web.evidence.runner import EvidenceAwareRunner, _cache_kind
from xiaowo_web.knowledge.semantic_cache import SemanticCache

Q = "中国科学技术大学图书馆开放时间"


# ---------- 1. 缓存层：kind / limitations / 分类型 TTL ----------

def test_web_entry_round_trips_kind_and_limitations(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "c.db", embedder=_fake_embedder(), threshold=0.5)
    limits = ["本次命中本校官方来源 2 条。", "其中 2 条为微信公众号文章，未核实。"]
    assert cache.store(Q, "每天 8:00-22:00。", "demo", kind="web", limitations=limits) is True
    hit = cache.lookup(Q, "demo")
    assert hit is not None
    assert hit["kind"] == "web"
    assert hit["limitations"] == limits


def test_web_entries_expire_sooner_than_local(tmp_path) -> None:
    cache = SemanticCache(
        tmp_path / "c.db", embedder=_fake_embedder(), threshold=0.5,
        ttl_seconds=86400.0, web_ttl_seconds=1800.0,
    )
    cache.store("学生证补办", "本地答案", "demo", kind="local", now=1000.0)
    cache.store("校车班车路线", "联网答案", "demo", kind="web", now=1000.0)

    # 31 分钟后：联网条目过期，本地条目仍在
    assert cache.lookup("校车班车路线", "demo", now=1000.0 + 1861) is None
    assert cache.lookup("学生证补办", "demo", now=1000.0 + 1861) is not None
    # 25 小时后：本地条目也过期
    assert cache.lookup("学生证补办", "demo", now=1000.0 + 90001) is None


def test_default_kind_is_local(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "c.db", embedder=_fake_embedder(), threshold=0.5)
    cache.store(Q, "答案", "demo")
    assert cache.lookup(Q, "demo")["kind"] == "local"


def test_legacy_table_gets_kind_and_limitations_columns(tmp_path) -> None:
    """老库（没有 kind/limitations 列）必须能自动迁移，且旧条目按 local 处理。"""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE semantic_cache(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
            embedding TEXT NOT NULL, source_hashes TEXT NOT NULL,
            sources TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO semantic_cache(namespace, question, answer, embedding, source_hashes, "
        "sources, created_at) VALUES(?,?,?,?,?,?,?)",
        ("demo", Q, "老库答案", json.dumps([0.0] * 7 + [1.0]), "[]", "[]", 2000.0),
    )
    conn.commit()
    conn.close()

    cache = SemanticCache(path, embedder=_fake_embedder(), threshold=0.5)
    hit = cache.lookup(Q, "demo", now=2001.0)
    assert hit is not None and hit["kind"] == "local" and hit["limitations"] == []
    # 新写入必须带上新列
    assert cache.store("学生证补办", "新答案", "demo", kind="web", limitations=["x"]) is True
    assert cache.lookup("学生证补办", "demo")["kind"] == "web"


# ---------- 2. 哪些最终答案可以进缓存 ----------

def _bundle(markdown="答案正文。", *, claims=None, sources=None, reason="local_answer",
            truncated=False) -> AnswerBundle:
    return AnswerBundle(
        markdown=markdown,
        claims=claims if claims is not None else [{"claim_id": "c1", "status": "confirmed"}],
        sources=sources if sources is not None else [{"level": "official_primary", "title": "教务处"}],
        terminal_reason=reason,
        truncated=truncated,
    )


def test_cache_kind_accepts_local_and_web() -> None:
    assert _cache_kind(_bundle()) == "local"
    web = _bundle(
        claims=[{"claim_id": "c1", "status": "generated"}],
        sources=[{"level": "official_primary", "title": "教务处"}],
        reason="AI_GENERATED",
    )
    assert _cache_kind(web) == "web"


def test_cache_kind_rejects_personal_refusal_truncated_and_web_without_trusted_source() -> None:
    personal = _bundle(sources=[{"level": "tool_result", "title": "个人课表"}])
    assert _cache_kind(personal) is None

    refusal = _bundle(claims=[{"claim_id": "c1", "status": "insufficient"}],
                      reason="AI_GENERATED")
    assert _cache_kind(refusal) is None

    assert _cache_kind(_bundle(truncated=True)) is None
    assert _cache_kind(_bundle(markdown="   ")) is None

    web_unverified = _bundle(
        claims=[{"claim_id": "c1", "status": "generated"}],
        sources=[{"level": "unverified", "title": "某公众号"}],
        reason="AI_GENERATED",
    )
    assert _cache_kind(web_unverified) is None, "只有未核实来源的联网答案不该冻结进缓存"

    cache_hit = _bundle(reason="cache_hit")
    assert _cache_kind(cache_hit) is None, "命中缓存的答案不必再写一次"


# ---------- 3. 外层 runner：写最终答案 ----------

class _RecordingCache:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def store(self, question, answer, namespace, **kwargs) -> bool:
        self.calls.append({"question": question, "answer": answer, "namespace": namespace, **kwargs})
        return True

    def lookup(self, *_a, **_k):
        return None


class _LocalStub:
    """本地 runner 桩：返回预设 bundle。"""

    def __init__(self, bundle: AnswerBundle) -> None:
        self.bundle = bundle

    async def run(self, _request) -> AnswerBundle:
        return self.bundle

    def close(self) -> None:
        return None


class _WebStub:
    """联网管线桩：返回预设 bundle，并记下收到的 local_hint。"""

    def __init__(self, bundle: AnswerBundle) -> None:
        self.bundle = bundle
        self.hints: list = []

    async def answer(self, _q, *, profile=None, on_stage=None, on_delta=None,
                     local_hint=None, rounds_limit=None) -> AnswerBundle:
        self.hints.append(local_hint)
        return self.bundle

    async def close(self) -> None:
        return None


def _request(question=Q, *, auth_mode="anonymous", student_id=None) -> QaRunRequest:
    principal = SimpleNamespace(
        auth_mode=auth_mode, is_authenticated=False, principal_id=student_id,
        profile={}, session_key="s1", history_owner_key="s1", review_namespace=None,
    )
    return SimpleNamespace(
        run_id="r1", question=question, requested_mode="auto", effective_mode="auto",
        principal=principal, conversation_id=None, chat_history=[],
        emit_stage=None, emit_delta=None, emit_table=None,
    )


def test_outer_runner_caches_final_web_answer_with_limitations() -> None:
    cache = _RecordingCache()
    web = _bundle(
        claims=[{"claim_id": "c1", "status": "generated"}],
        sources=[{"level": "official_primary", "title": "教务处"},
                 {"level": "unverified", "title": "某公众号"}],
        reason="AI_GENERATED",
    )
    web.limitations = ["其中 1 条为微信公众号文章，未核实。"]
    # 本地"未答出来"（claims 为空 → 必然走联网）
    local = _bundle(markdown="本地没查到。", claims=[])
    runner = EvidenceAwareRunner(_LocalStub(local), _WebStub(web), semantic_cache=cache)  # type: ignore[arg-type]
    result = asyncio.run(runner.run(_request()))

    assert result is web
    assert len(cache.calls) == 1
    call = cache.calls[0]
    assert call["kind"] == "web"
    assert call["namespace"] == "production"
    # 本地草稿会被当 local_hint 带进联网合成，runner 因此额外追加一条"合并披露"
    assert "其中 1 条为微信公众号文章，未核实。" in call["limitations"]
    assert call["source_hashes"] == [], "联网条目没有 chunk hash"


def test_outer_runner_caches_local_answer_with_hashes() -> None:
    cache = _RecordingCache()
    local = _bundle()
    local.cache_source_hashes = ["hash-a", "hash-b"]
    runner = EvidenceAwareRunner(_LocalStub(local), _WebStub(local), semantic_cache=cache)  # type: ignore[arg-type]
    asyncio.run(runner.run(_request()))
    call = cache.calls[0]
    assert call["kind"] == "local"
    assert call["source_hashes"] == ["hash-a", "hash-b"], "发布激活时的定向失效要靠它"


def test_outer_runner_skips_time_sensitive_and_personal() -> None:
    cache = _RecordingCache()
    local = _bundle()
    runner = EvidenceAwareRunner(_LocalStub(local), _WebStub(local), semantic_cache=cache)  # type: ignore[arg-type]
    asyncio.run(runner.run(_request("最新的转专业政策是什么")))
    assert cache.calls == [], "时效问句宁可每次重查"

    personal = _bundle(sources=[{"level": "tool_result", "title": "个人课表"}])
    runner2 = EvidenceAwareRunner(_LocalStub(personal), _WebStub(personal), semantic_cache=cache)  # type: ignore[arg-type]
    asyncio.run(runner2.run(_request("我今天有什么课")))
    assert cache.calls == [], "个人数据绝不进缓存"


def test_outer_runner_without_cache_is_noop() -> None:
    local = _bundle()
    runner = EvidenceAwareRunner(_LocalStub(local), _WebStub(local))
    assert asyncio.run(runner.run(_request())) is local


# ---------- 4. 命中路径：区分 local / web ----------

class _StubCache:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.stored: list = []

    def lookup(self, _question, _namespace):
        return self.payload

    def store(self, *args, **kwargs) -> bool:
        self.stored.append((args, kwargs))
        return True


def _hit(payload: dict) -> AnswerBundle:
    runner = LegacyQaRunner(run_qa_func=lambda *_a, **_k: {}, semantic_cache=_StubCache(payload))
    return asyncio.run(runner.run(_request()))


def test_cache_hit_restores_limitations_and_marks_web_as_generated() -> None:
    limits = ["其中 2 条为微信公众号文章，无法核实发布方。"]
    result = _hit({
        "answer": "联网答案。", "score": 0.97, "kind": "web", "limitations": limits,
        "sources": [{"source_id": "s1", "title": "教务处", "level": "official_primary"}],
    })
    assert result.terminal_reason == "cache_hit"
    assert result.claims[0]["status"] == "generated", "联网缓存不能冒充已确认"
    assert any("最多缓存 30 分钟" in item for item in result.limitations)
    assert limits[0] in result.limitations, "当初的限制说明必须还原"


def test_cache_hit_keeps_local_as_confirmed() -> None:
    result = _hit({
        "answer": "本地答案。", "score": 0.97, "kind": "local", "limitations": [],
        "sources": [{"source_id": "s1", "title": "教务处", "level": "official_primary"}],
    })
    assert result.claims[0]["status"] == "confirmed"
    assert not any("最多缓存 30 分钟" in item for item in result.limitations)


# ---------- 5. 本地 runner 的延迟写入开关 ----------

def test_local_runner_can_defer_cache_write(tmp_path) -> None:
    cache = SemanticCache(tmp_path / "c.db", embedder=_fake_embedder(), threshold=0.5)
    deferring = LegacyQaRunner(run_qa_func=lambda *_a, **_k: {}, semantic_cache=cache,
                               defer_cache_write=True)
    assert deferring._defer_cache_write is True
    writing = LegacyQaRunner(run_qa_func=lambda *_a, **_k: {}, semantic_cache=cache)
    assert writing._defer_cache_write is False

# ---------- 6. 缓存命中 + 联网拒答 → 回退缓存答案（不能端出 88 字拒答） ----------

def test_cache_hit_falls_back_to_cached_answer_when_web_refuses() -> None:
    cached_local = AnswerBundle(
        markdown="教秘：王海龙，电话 0551-63600913。",
        claims=[{"claim_id": "c1", "status": "confirmed"}],
        sources=[{"level": "official_primary", "title": "教学秘书联系方式及办公地点"}],
        terminal_reason="cache_hit",
    )
    refusal = AnswerBundle(
        markdown="本次检索到的内容与问题不相关，已改为固定答复。",
        claims=[{"claim_id": "c1", "status": "insufficient"}],
        terminal_reason="EVIDENCE_INSUFFICIENT",
    )
    runner = EvidenceAwareRunner(_LocalStub(cached_local), _WebStub(refusal))  # type: ignore[arg-type]
    result = asyncio.run(runner.run(_request()))
    assert result is cached_local, "缓存里有好答案时不能端出联网拒答"
    assert any("回退" in item or "可能不是最新" in item for item in result.limitations)

# ---------- 7. web 类缓存命中要短路（不再跑联网） ----------

def test_web_cache_hit_short_circuits_without_web_call() -> None:
    cached = AnswerBundle(
        markdown="联网缓存答案。",
        claims=[{"claim_id": "c1", "status": "generated"}],
        sources=[{"level": "official_primary", "title": "教务处"}],
        terminal_reason="cache_hit",
        cache_kind="web",
    )
    pipeline = _WebStub(_bundle())
    runner = EvidenceAwareRunner(_LocalStub(cached), pipeline)  # type: ignore[arg-type]
    result = asyncio.run(runner.run(_request()))
    assert result is cached
    assert pipeline.hints == [], "联网缓存命中不该再跑一次联网"


def test_local_cache_hit_still_consults_web(monkeypatch) -> None:
    """local 类命中可能是"未收录"半成品 → 仍要判定器把关（判否就去联网）。"""
    import xiaowo_web.evidence.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_llm_judge_answered", lambda *_a, **_k: False)
    cached = AnswerBundle(
        markdown="知识库里没有收录。",
        claims=[{"claim_id": "c1", "status": "confirmed"}],
        sources=[{"level": "official_primary", "title": "教务处"}],
        terminal_reason="cache_hit",
        cache_kind="local",
    )
    pipeline = _WebStub(_bundle(claims=[{"claim_id": "c1", "status": "generated"}],
                                reason="AI_GENERATED"))
    runner = EvidenceAwareRunner(_LocalStub(cached), pipeline)  # type: ignore[arg-type]
    result = asyncio.run(runner.run(_request()))
    assert pipeline.hints != [], "local 类命中仍应交给联网补齐"
    assert result is pipeline.bundle
