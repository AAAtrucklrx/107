"""Phase 2 影子对比：只记录、绝不影响线上（2026-09-17）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from xiaowo_web.evidence.shadow import (
    ShadowComparer,
    ShadowStore,
    build_web_candidates_summary,
    evidence_facts,
    unsupported_facts,
    verifiable_ratio,
)


def _principal():
    return SimpleNamespace(auth_mode="demo", is_authenticated=True, profile={})


def _request(question: str = "中国科学技术大学图书馆开放时间？"):
    return SimpleNamespace(run_id="run-1", question=question, principal=_principal())


def _a_bundle(markdown: str = "A 的答案", references=None):
    return SimpleNamespace(
        markdown=markdown,
        limitations=["A 的限制"],
        web_references=list(references if references is not None else []),
    )


class _Search:
    def __init__(self, refs, *, boom: bool = False) -> None:
        self.refs = refs
        self.boom = boom
        self.calls: list[str] = []

    async def references_only(self, query, *, site="", limit=8):
        self.calls.append(query)
        if self.boom:
            raise RuntimeError("pure search down")
        return self.refs


class _Shadow:
    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[dict] = []
        self.boom = boom

    async def compare(self, **kwargs):
        self.calls.append(kwargs)
        if self.boom:
            raise RuntimeError("shadow down")
        return "shadow-1"


# ---------------------------------------------------------------- 指标

def test_fact_metrics_only_track_numbers_and_dates() -> None:
    """可核验性代理指标只覆盖**数字/日期类**事实（名字/地名要 LLM 判官，这里不假装覆盖）。"""
    answer = "图书馆每周开放 7*16 小时，电话 63607647，2026年9月14日 起调整。"
    facts = evidence_facts(answer)
    assert "7*16" in facts and "63607647" in facts and "2026" in facts

    # 证据里完全没有 → 全部不可核验
    assert verifiable_ratio(answer, "图书馆提供网络信息服务。") == 0.0
    assert set(unsupported_facts(answer, "图书馆提供网络信息服务。")) >= {"7*16", "63607647"}

    # 证据里有 → 1.0
    full = "每周开放 7*16 小时，电话 63607647，2026年9月14日起调整。"
    assert verifiable_ratio(answer, full) == 1.0

    # 无数字事实 → None（不参与平均）
    assert verifiable_ratio("小蜗来啦～", "任何证据") is None


def test_web_candidates_summary_keeps_much_more_than_local_path() -> None:
    """联网候选每条给到 1500 字（本地知识库路径只给 120 字）——换成纯搜索的意义所在。"""
    refs = [{"title": "图书馆", "url": "https://lib.ustc.edu.cn/a", "content": "开" * 3000}]
    summary = build_web_candidates_summary(refs)
    assert summary.count("开") == 1500
    assert "《图书馆》" in summary and "https://lib.ustc.edu.cn/a" in summary


# ---------------------------------------------------------------- 记录库

def test_shadow_store_roundtrip(tmp_path) -> None:
    store = ShadowStore(tmp_path / "shadow.db")
    store.initialize()
    run_id = store.record({
        "question": "问题", "source": "batch",
        "a_answer": "A", "a_latency": 30.5, "a_ref_chars": 1624,
        "b_answer": "B", "b_search_latency": 0.7, "b_compose_latency": 2.1,
        "b_ref_chars": 8000, "b_unsupported": ["9999"],
    })
    assert run_id.startswith("shadow-")
    row = store.rows()[0]
    assert row["a_latency"] == 30.5 and row["b_compose_latency"] == 2.1
    assert row["b_ref_chars"] == 8000
    assert "9999" in row["b_unsupported"]


# ---------------------------------------------------------------- 对比器

def test_comparer_records_both_sides(tmp_path) -> None:
    store = ShadowStore(tmp_path / "shadow.db")
    store.initialize()
    refs = [{"url": "https://lib.ustc.edu.cn/a", "title": "图书馆", "content": "每周开放 7*16 小时，电话 63607647。"}]
    comparer = ShadowComparer(store, _Search(refs), compose=lambda q, r: "B 的答案：每周开放 7*16 小时。")
    run_id = asyncio.run(comparer.compare(
        question="图书馆开放时间？",
        a_answer="A 的答案：每周开放 7*16 小时，电话 63607647，还有 8888 这个数字。",
        a_references=[{"url": "u", "content": "每周开放 7*16 小时。"}],
        a_latency=42.0,
        a_limitations=["A 限制"],
    ))
    assert run_id
    row = store.rows()[0]
    assert row["a_latency"] == 42.0
    assert row["a_ref_chars"] == len("每周开放 7*16 小时。")
    assert row["b_answer"] == "B 的答案：每周开放 7*16 小时。"
    assert row["b_search_latency"] is not None and row["b_compose_latency"] is not None
    # A 的答案里有 8888，但 A 的证据里没有 → A 的不可核验事实被记下来
    assert "8888" in row["a_unsupported"]
    assert row["b_error"] is None


def test_comparer_never_raises_on_failure(tmp_path) -> None:
    """影子链路失败只记 b_error，绝不抛给调用方（线上回答已经返回给用户了）。"""
    store = ShadowStore(tmp_path / "shadow.db")
    store.initialize()
    comparer = ShadowComparer(store, _Search([], boom=True))
    run_id = asyncio.run(comparer.compare(
        question="q", a_answer="A", a_references=[{"url": "u", "content": "c"}],
    ))
    assert run_id
    row = store.rows()[0]
    assert row["b_answer"] is None
    assert "pure search down" in (row["b_error"] or "")


# ---------------------------------------------------------------- runner 钩子

def test_shadow_hook_requires_smart_references() -> None:
    """只有确实走了智能搜索（有原始 references）才记影子；本地答案不记。"""
    from xiaowo_web.evidence.runner import EvidenceAwareRunner

    async def scenario() -> _Shadow:
        shadow = _Shadow()
        runner = EvidenceAwareRunner(object(), object(), shadow=shadow)
        runner._shadow_compare(_request(), _a_bundle(references=[]), 1.0)
        assert shadow.calls == [], "本地答案不该产生影子记录"
        runner._shadow_compare(
            _request(), _a_bundle(references=[{"url": "u", "content": "c"}]), 3.0
        )
        await asyncio.gather(*runner._shadow_tasks)   # 等后台任务跑完
        return shadow

    shadow = asyncio.run(scenario())
    assert len(shadow.calls) == 1
    call = shadow.calls[0]
    assert call["namespace"] == "demo" and call["source"] == "production"
    assert call["a_latency"] == 3.0
    assert call["question"] == "中国科学技术大学图书馆开放时间？"


def test_shadow_hook_swallows_compare_errors() -> None:
    """影子任务内部失败必须被消化，不留未处理异常。"""
    from xiaowo_web.evidence.runner import EvidenceAwareRunner

    async def scenario() -> None:
        runner = EvidenceAwareRunner(object(), object(), shadow=_Shadow(boom=True))
        runner._shadow_compare(
            _request(), _a_bundle(references=[{"url": "u", "content": "c"}]), 1.0
        )
        await asyncio.gather(*runner._shadow_tasks, return_exceptions=False)

    asyncio.run(scenario())   # 不抛异常即通过


def test_shadow_hook_is_noop_without_event_loop() -> None:
    """同步上下文（无事件循环）不得产生未 await 的协程警告。"""
    from xiaowo_web.evidence.runner import EvidenceAwareRunner

    runner = EvidenceAwareRunner(object(), object(), shadow=_Shadow())
    runner._shadow_compare(_request(), _a_bundle(references=[{"url": "u", "content": "c"}]), 1.0)
    assert runner._shadow_tasks == set()
