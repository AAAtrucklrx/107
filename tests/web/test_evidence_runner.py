"""EvidenceAwareRunner 联网/本地决策门的回归测试（2026-09-02 修复）。

覆盖三类症状的根因：
1. 寒暄/无 factual claim 的本地回答被误判未命中 → 无条件触发联网；
2. 联网证据不足时本地已命中的正确回答被丢弃；
3. 时效性问题联网失败后诚实告知而非回退可能过期的数据。
"""

from __future__ import annotations

import asyncio

import pytest

from xiaowo_web.auth.models import Principal
from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.evidence.pipeline import EvidencePipeline
from xiaowo_web.evidence import runner as runner_mod
from xiaowo_web.evidence.runner import EvidenceAwareRunner


def _request(question: str, mode: str = "auto") -> QaRunRequest:
    return QaRunRequest(
        run_id="run-1",
        question=question,
        requested_mode=mode,
        effective_mode=mode,
        principal=Principal("", "anonymous", {}, False, "session-1"),
        conversation_id=None,
    )


def _claim(kind: str = "factual", status: str = "confirmed") -> dict:
    return {"claim_id": "c1", "text": "回答", "kind": kind, "status": status, "evidence": []}


class _FixedRunner:
    """本地 runner 桩：返回预设 bundle，并记录调用次数。"""

    def __init__(self, bundle: AnswerBundle) -> None:
        self.bundle = bundle
        self.calls = 0

    async def run(self, _request: QaRunRequest) -> AnswerBundle:
        self.calls += 1
        return self.bundle

    def close(self) -> None:
        return None


class _StubPipeline:
    """联网管线桩：返回预设 bundle，并记录调用次数。"""

    def __init__(self, bundle: AnswerBundle | None = None, error: Exception | None = None) -> None:
        self.bundle = bundle or AnswerBundle(
            markdown="暂未找到足够可靠的联网证据。",
            terminal_reason="EVIDENCE_INSUFFICIENT",
        )
        self.error = error
        self.calls = 0

    async def answer(self, _question: str, *, profile=None, on_stage=None, rounds_limit=None) -> AnswerBundle:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.bundle

    async def close(self) -> None:
        return None


def _build(local_bundle: AnswerBundle, pipeline: _StubPipeline) -> tuple[EvidenceAwareRunner, _FixedRunner]:
    local = _FixedRunner(local_bundle)
    runner = EvidenceAwareRunner(local, pipeline)  # type: ignore[arg-type]
    return runner, local


def _local_bundle(claims: list[dict], *, reason: str = "local_answer") -> AnswerBundle:
    return AnswerBundle(
        markdown="本地知识库的可靠回答。",
        claims=claims,
        terminal_reason=reason,
    )


def test_confirmed_local_answer_skips_web_search() -> None:
    """本地知识命中（factual+confirmed）→ 不触发联网。"""
    pipeline = _StubPipeline()
    runner, local = _build(_local_bundle([_claim("factual", "confirmed")]), pipeline)
    bundle = asyncio.run(runner.run(_request("学生证丢了怎么办？")))
    assert local.calls == 1
    assert pipeline.calls == 0
    assert bundle.terminal_reason == "local_answer"


def test_chitchat_answer_does_not_trigger_web_search() -> None:
    """回归：寒暄类回答（非 factual claim）不再被误判为未命中。"""
    pipeline = _StubPipeline()
    runner, local = _build(_local_bundle([_claim("chitchat", "confirmed")]), pipeline)
    bundle = asyncio.run(runner.run(_request("你好呀")))
    assert pipeline.calls == 0
    assert bundle.terminal_reason == "local_answer"


def test_tool_result_answer_does_not_trigger_web_search() -> None:
    """工具结果（实时校园数据）带时效词也不联网核对——数据本身就是系统实时返回。"""
    pipeline = _StubPipeline()
    bundle = _local_bundle([_claim("factual", "confirmed")])
    bundle.sources = [{"level": "tool_result", "title": "考试安排", "citation": 1}]
    runner, _ = _build(bundle, pipeline)
    result = asyncio.run(runner.run(_request("查一下今天的考试安排")))
    assert pipeline.calls == 0
    assert result.terminal_reason == "local_answer"


def test_local_miss_with_web_confirmed_uses_web_answer() -> None:
    """本地未命中 → 联网命中 → 采用联网回答。"""
    web_bundle = AnswerBundle(
        markdown="联网核验后的回答。",
        claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    )
    pipeline = _StubPipeline(web_bundle)
    runner, local = _build(
        _local_bundle([_claim("factual", "insufficient")]), pipeline,
    )
    bundle = asyncio.run(runner.run(_request("今天的新闻")))
    assert pipeline.calls == 1
    assert bundle.terminal_reason == "web_evidence_confirmed"


def test_double_miss_falls_back_to_local_answer() -> None:
    """本地未答出 + 联网证据不足 → 回退本地回答而非拒答（非时效问句）。"""
    pipeline = _StubPipeline()  # EVIDENCE_INSUFFICIENT
    local_bundle = _local_bundle([_claim("factual", "insufficient")])
    runner, _ = _build(local_bundle, pipeline)
    bundle = asyncio.run(runner.run(_request("科大有哪些校园服务？")))
    assert pipeline.calls == 1
    assert bundle.terminal_reason == "local_answer"
    assert any("联网证据不足" in item for item in bundle.limitations)


# ── 本地优先（2026-09-16 改）：本地答得出来就不联网，时效问句也一样 ──────

def test_confirmed_local_answer_wins_even_for_current_questions() -> None:
    """核心回归：本地已确认 → **不联网**，即便问句带时效词。

    原行为是「时效词强制联网」，把本地官方内容顶掉：实测「最新的转专业政策是什么」
    本地 claim=confirmed、来源含 official_primary，却被换成了百度智能搜索生成的一段
    **天津科技大学**内容（检索把"科大"匹配到了别校的"AI科大"平台）。
    """
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答（可能来自别的学校）。",
        claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    runner, _ = _build(_local_bundle([_claim("factual", "confirmed")]), pipeline)
    bundle = asyncio.run(runner.run(_request("现在的校历安排是什么？")))
    assert pipeline.calls == 0, "本地已确认就不该联网"
    assert bundle.terminal_reason == "local_answer"
    assert any("可能不是最新" in item for item in bundle.limitations), "须如实标注时效"


def test_local_not_ready_falls_through_to_web() -> None:
    """本地答不出 → 仍然联网兜底（本地优先不等于不联网）。"""
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。",
        claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    runner, _ = _build(_local_bundle([_claim("factual", "insufficient")]), pipeline)
    bundle = asyncio.run(runner.run(_request("科大有哪些校园服务？")))
    assert pipeline.calls == 1
    assert bundle.terminal_reason == "web_evidence_confirmed"


def test_web_mode_is_explicit_force_web_backdoor() -> None:
    """「纯联网」已不是可选模式，只作「强制联网重答」的显式后门：绕过本地优先。"""
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。",
        claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    runner, local = _build(_local_bundle([_claim("factual", "confirmed")]), pipeline)
    bundle = asyncio.run(runner.run(_request("现在的校历安排是什么？", mode="web")))
    assert pipeline.calls == 1
    assert local.calls == 0, "后门必须绕过本地"
    assert bundle.terminal_reason == "web_evidence_confirmed"


def test_current_question_double_miss_stays_honest() -> None:
    """强时效问题双未命中 → 不回退可能过期的答案，诚实告知（不编造）。"""
    pipeline = _StubPipeline(AnswerBundle(markdown="暂未找到足够可靠的联网证据。", terminal_reason="CRAWL_BLOCKED"))
    runner, _ = _build(
        _local_bundle([_claim("factual", "insufficient")]), pipeline,
    )
    bundle = asyncio.run(runner.run(_request("截至今日最新的保研政策是什么？")))
    assert pipeline.calls == 1
    assert bundle.terminal_reason == "CRAWL_BLOCKED"
    assert bundle.markdown.startswith("暂未找到足够可靠的联网证据")


def test_local_mode_bypasses_pipeline() -> None:
    """显式 local 模式直通本地，绝不触发联网。"""
    pipeline = _StubPipeline()
    runner, local = _build(
        _local_bundle([_claim("factual", "insufficient")]), pipeline,
    )
    bundle = asyncio.run(runner.run(_request("本地模式问题", mode="local")))
    assert pipeline.calls == 0
    assert bundle.terminal_reason == "local_answer"


def test_normalized_quote_matching_allows_punctuation_drift() -> None:
    """_verify_claims：quote 与页面文本存在全半角/空白/标点漂移时仍可匹配。"""
    from types import SimpleNamespace

    from xiaowo_web.evidence.models import (
        CrawledPage,
        ExtractedClaim,
        ExtractedEvidence,
        TrustDecision,
        ValidatedUrl,
    )
    from xiaowo_web.evidence.pipeline import EvidencePipeline, _PageRecord

    url = ValidatedUrl(
        normalized_url="https://lib.ustc.edu.cn/hours", scheme="https",
        host="lib.ustc.edu.cn", port=443, path="/hours", approved_ips=(), ustc_domain=True,
    )
    trust = TrustDecision(level="official_primary", institution="图书馆")
    page = CrawledPage(
        requested_url=url.normalized_url,
        final_url=url.normalized_url,
        title="开放时间",
        markdown="开放安排：图书馆周一至周日　8：00开放（节假日除外）。详见馆内公告。",
        status_code=200,
        content_type="text/markdown",
        fetched_at="2026-09-02T00:00:00Z",
        published_at="2026-09-01T00:00:00Z",
        content_hash="h",
        robots_allowed=True,
        peer_ip_verified=True,
    )
    record = _PageRecord(source_id="s-lib", url=url, trust=trust, page=page, citation=0)
    extracted = [ExtractedClaim(
        text="图书馆周一至周日 8:00 开放。",
        evidence=(ExtractedEvidence(
            source_id="s-lib",
            relation="supports",
            quote="图书馆周一至周日8:00开放,节假日除外",
        ),),
    )]

    pipeline = EvidencePipeline.__new__(EvidencePipeline)
    pipeline.settings = SimpleNamespace(web_query_rewrite=False)
    claims, confirmed_lines, _conflict, _spans, _notes = pipeline._verify_claims(extracted, [record])

    assert claims and claims[0]["status"] == "confirmed"
    assert confirmed_lines and "图书馆周一至周日" in confirmed_lines[0]


# ── 「本地答了个没有」不算答出来（2026-09-16）────────────────────────
# 本地会把"我这边暂时没有查到…"也标成 claim=confirmed，若据此判定"本地答得出来"，
# 该联网的问题就永远不联网。实测「今天合肥天气怎么样」「国家助学贷款新政」都中招。

def test_local_no_answer_falls_through_to_web() -> None:
    """本地只回了句"查不到" → 不算答出来，应落到联网兜底。"""
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。", claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    local = AnswerBundle(
        markdown="小蜗这边暂时没有查到相关的实时数据，建议你用天气 App 查询。",
        claims=[_claim("factual", "confirmed")],
        terminal_reason="local_answer",
    )
    runner, _ = _build(local, pipeline)
    bundle = asyncio.run(runner.run(_request("今天合肥天气怎么样？")))
    assert pipeline.calls == 1, "本地没答出来就该联网"
    assert bundle.terminal_reason == "web_evidence_confirmed"


def test_local_no_answer_with_tool_source_stays_local() -> None:
    """工具返回的"没有记录"是真实结果，联网更查不到 → 必须留在本地。"""
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。", claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    local = AnswerBundle(
        markdown="暂时没有查到你的考试安排。",
        claims=[_claim("factual", "confirmed")],
        sources=[{"level": "tool_result", "title": "考试安排"}],
        terminal_reason="local_answer",
    )
    runner, _ = _build(local, pipeline)
    bundle = asyncio.run(runner.run(_request("我的考试安排")))
    assert pipeline.calls == 0
    assert bundle.terminal_reason == "local_answer"


def test_real_answer_mentioning_gap_stays_local() -> None:
    """答出来了、只是附带说明数据不全 → 不得误判为"没答出来"。"""
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。", claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    local = AnswerBundle(
        markdown="根据校历，国庆节放假 3 天。校历未列出具体起止日期，以学校通知为准。",
        claims=[_claim("factual", "confirmed")],
        terminal_reason="local_answer",
    )
    runner, _ = _build(local, pipeline)
    bundle = asyncio.run(runner.run(_request("2026年国庆节放假安排")))
    assert pipeline.calls == 0, "真的答出来了就不该联网"
    assert bundle.terminal_reason == "local_answer"


def test_structural_signal_no_recall_falls_through_to_web() -> None:
    """结构信号：知识库**没召回任何材料** → 没答出来，落到联网（可靠判据）。"""
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。", claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    local = AnswerBundle(
        markdown="已根据现有资料为你整理如下。",   # 措辞上**没有**否定词
        claims=[_claim("factual", "confirmed")],
        retrieval={"candidates_found": False, "candidate_count": 0, "top_score": 0.0},
        terminal_reason="local_answer",
    )
    runner, _ = _build(local, pipeline)
    bundle = asyncio.run(runner.run(_request("科大有哪些校园服务？")))
    assert pipeline.calls == 1, "知识库没召回就该联网"
    assert bundle.terminal_reason == "web_evidence_confirmed"


@pytest.fixture(autouse=True)
def _stub_answer_judge(monkeypatch):
    """把 LLM 判定器替换为"不可用"（返回 None）→ 单测走措辞启发式，保持离线与确定性。

    判定器本身的行为由下面两个用例单独覆盖。
    """
    monkeypatch.setattr(runner_mod, "_llm_judge_answered", lambda q, a: None)


def test_answer_judge_not_answered_falls_through_to_web(monkeypatch) -> None:
    """判定器说"没回答" → 即使措辞里没有否定词，也要落到联网。"""
    monkeypatch.setattr(runner_mod, "_llm_judge_answered", lambda q, a: False)
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。", claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    local = AnswerBundle(
        markdown="已为你把相关要点整理如下，供参考。",   # 无任何否定措辞
        claims=[_claim("factual", "confirmed")],
        terminal_reason="local_answer",
    )
    runner, _ = _build(local, pipeline)
    bundle = asyncio.run(runner.run(_request("科大有哪些校园服务？")))
    assert pipeline.calls == 1
    assert bundle.terminal_reason == "web_evidence_confirmed"


def test_answer_judge_answered_keeps_local_even_with_negative_wording(monkeypatch) -> None:
    """判定器说"回答了" → 判定优先于措辞启发式（措辞只是兜底）。"""
    monkeypatch.setattr(runner_mod, "_llm_judge_answered", lambda q, a: True)
    pipeline = _StubPipeline(AnswerBundle(
        markdown="联网回答。", claims=[_claim("factual", "confirmed")],
        terminal_reason="web_evidence_confirmed",
    ))
    local = AnswerBundle(
        markdown="暂时没有查到具体日期，但放假 3 天已经确认。",
        claims=[_claim("factual", "confirmed")],
        terminal_reason="local_answer",
    )
    runner, _ = _build(local, pipeline)
    bundle = asyncio.run(runner.run(_request("2026年国庆节放假安排")))
    assert pipeline.calls == 0
    assert bundle.terminal_reason == "local_answer"
