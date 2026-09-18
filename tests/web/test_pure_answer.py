"""纯搜索 + 两道闸门 + 自研合成（web_answer_mode=pure，2026-09-17）。

为什么不用端点的"智能搜索生成"直接出答案：它是黑盒，实测答案里的数字大量不在它
披露给我们的证据里（可核验 0.342），我们无法核验。自己合成才能做这两道闸门：

1. **相关性闸门（合成前）**：实测「食堂开放时间」纯搜索返回的全是教务处选课/竞赛，
   不拦就会像实测那样"垃圾证据也硬编"；
2. **可核验性闸门（合成后）**：答案里的数字/日期必须都能在证据里找到。

任一道不过 → `terminal_reason=EVIDENCE_INSUFFICIENT`（**这一步至关重要**：runner 的
本地回退以该终态为条件，漏掉就会把 88 字拒答直接推给用户）。
"""

from __future__ import annotations

import asyncio

import pytest

from tests.web.helpers import make_settings
from tests.web.test_evidence_pipeline import FakeCrawler, FixedExtractor
from xiaowo_web.evidence.models import SearchBatch
from xiaowo_web.evidence.pipeline import EvidencePipeline
from xiaowo_web.evidence.url_security import UrlGuard

_REFS = [
    {"url": "https://lib.ustc.edu.cn/a", "title": "图书馆公告",
     "content": "图书馆每周开放 7*16 小时，电话 63607647。", "date": "2026-09-14"},
    {"url": "https://lib.ustc.edu.cn/b", "title": "研讨室预约",
     "content": "研讨室通过图书馆主页预约，每天 8:00-22:00。", "date": "2026-09-14"},
]


class PureSearch:
    """带 references_only() 的搜索替身（纯搜索模式）。"""

    def __init__(self, *, references=None, error: Exception | None = None) -> None:
        self.references = list(_REFS if references is None else references)
        self.error = error
        self.calls: list[dict] = []

    async def references_only(self, query: str, *, site: str = "", limit: int = 8,
                              timeout: float = 20.0) -> list[dict]:
        self.calls.append({"query": query, "site": site, "limit": limit})
        if self.error is not None:
            raise self.error
        return list(self.references)

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        return SearchBatch([])

    async def close(self) -> None:
        return None


def _settings(tmp_path, *, mode: str = "pure"):
    return make_settings(tmp_path, extra={
        "XIAOWO_SEARCH_PROVIDER": "baidu",
        "XIAOWO_BAIDU_SEARCH_KEY": "bce-v3/test-key",
        "XIAOWO_WEB_ANSWER_MODE": mode,
    })


def _pipeline(tmp_path, search, *, mode: str = "pure") -> EvidencePipeline:
    return EvidencePipeline(
        _settings(tmp_path, mode=mode),
        search=search,
        crawler=FakeCrawler({}),
        extractor=FixedExtractor([]),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
    )


@pytest.fixture()
def patched(monkeypatch):
    """把两道闸门与合成替换成可控替身（避免测试真的调 LLM）。"""
    import xiaowo_web.evidence.pipeline as pipeline_module

    state = {"relevant": True, "answer": "图书馆每周开放 7*16 小时，电话 63607647。", "unsupported": []}

    monkeypatch.setattr(pipeline_module, "judge_relevance",
                        lambda question, refs, **kw: state["relevant"])
    # 替身必须收 **kw：生产代码会传 report=[] 让合成把"对冲了几处"写回限制说明
    monkeypatch.setattr(pipeline_module, "compose_and_verify",
                        lambda question, refs, **kw: (state["answer"], list(state["unsupported"])))
    return state


def test_pure_answer_composes_when_relevant(tmp_path, patched) -> None:
    search = PureSearch()
    bundle = asyncio.run(_pipeline(tmp_path, search).answer("中国科学技术大学图书馆开放时间？"))
    assert bundle.terminal_reason == "AI_GENERATED"
    assert bundle.markdown == patched["answer"]
    # 检索词用**原问题**（不是 campus_search_keywords：那套会把「教务处 官方」拼进去，
    # 实测会把真正讲问题的页面挤出前 8 名）
    assert search.calls[0]["query"] == "中国科学技术大学图书馆开放时间？"
    # 来源按域名分层照旧可用，且说明改成"联网检索 + 自研合成"
    assert [s["domain"] for s in bundle.sources] == ["lib.ustc.edu.cn", "lib.ustc.edu.cn"]
    assert any("自研合成" in item for item in bundle.limitations)
    # 进料照旧透传（两道路径共用 _references_bundle）
    assert bundle.ingestion_urls == ["https://lib.ustc.edu.cn/a", "https://lib.ustc.edu.cn/b"]
    assert bundle.web_references


def test_irrelevant_evidence_is_refused(tmp_path, patched) -> None:
    """相关性闸门：refs 跑题就不合成（实测食堂题 refs 全是选课/竞赛）。"""
    patched["relevant"] = False
    bundle = asyncio.run(_pipeline(tmp_path, PureSearch()).answer("中国科学技术大学食堂开放时间"))
    assert bundle.terminal_reason == "EVIDENCE_INSUFFICIENT", "必须标成证据不足，否则 runner 不会回退本地"
    assert bundle.sources == []
    assert [c["status"] for c in bundle.claims] == ["insufficient"]
    assert any("不相关" in item for item in bundle.limitations)


def test_unverifiable_answer_is_refused(tmp_path, patched) -> None:
    """可核验性闸门：答案里有证据中找不到的数字 → 拒答，不展示无据内容。"""
    patched["unsupported"] = ["9999"]
    bundle = asyncio.run(_pipeline(tmp_path, PureSearch()).answer("中国科学技术大学图书馆开放时间？"))
    assert bundle.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert "没有查到可靠的数据" in bundle.markdown
    assert any("无法核实" in item for item in bundle.limitations)


def test_repair_removes_unsupported_numbers(tmp_path, monkeypatch) -> None:
    """发现有依据缺失的**先改一版**（删掉/改成"未给出"），而不是整篇拒答。"""
    import xiaowo_web.evidence.compose as compose_module

    calls = {"repair": 0}

    monkeypatch.setattr(compose_module, "compose_with_own_llm",
                        lambda q, r, on_delta=None: "图书馆每周开放 9*9 小时，电话 63607647。")
    monkeypatch.setattr(compose_module, "injected_context", lambda: "")
    monkeypatch.setattr(compose_module, "repair_answer",
                        lambda q, r, prev, bad: calls.__setitem__("repair", calls["repair"] + 1)
                        or "图书馆每周开放时间未在资料中给出，电话 63607647。")

    answer, unsupported = compose_module.compose_and_verify("图书馆开放时间？", _REFS)
    assert calls["repair"] == 1, "应触发一次修复"
    assert unsupported == [], "修好之后不应再有无法核实的数字"
    assert "9*9" not in answer and "63607647" in answer


def test_repair_gives_up_after_one_attempt(tmp_path, monkeypatch) -> None:
    """改一版仍不合格 → 返回剩余问题（由调用方给"没查到数据"的答复），不再反复重试。"""
    import xiaowo_web.evidence.compose as compose_module

    calls = {"repair": 0}
    monkeypatch.setattr(compose_module, "compose_with_own_llm", lambda q, r, on_delta=None: "开放 9*9 小时。")
    monkeypatch.setattr(compose_module, "injected_context", lambda: "")
    monkeypatch.setattr(compose_module, "repair_answer",
                        lambda q, r, prev, bad: calls.__setitem__("repair", calls["repair"] + 1)
                        or "开放 8*8 小时。")

    _answer, unsupported = compose_module.compose_and_verify("图书馆开放时间？", _REFS)
    assert calls["repair"] == 1, "只改一次，不无限重试"
    assert unsupported, "仍不合格时必须把问题交给调用方"


def test_search_failure_returns_to_caller(tmp_path, patched) -> None:
    """检索失败 → 返回 None（交给调用方回退经典链路），不产出半成品 bundle。"""
    bundle = asyncio.run(
        _pipeline(tmp_path, PureSearch(error=RuntimeError("boom"))).answer("问题")
    )
    # 经典链路在本测试夹具下拿不到结果 → 最终是拒绝/不足；关键是没崩、也没假装成功
    assert bundle.terminal_reason in {"EVIDENCE_INSUFFICIENT", "CRAWL_BLOCKED", "CLASSIC_FAILED", "NO_CANDIDATES"}


def test_mode_smart_does_not_call_pure(tmp_path, monkeypatch) -> None:
    """开关：web_answer_mode=smart 时必须走旧路径（保留回退能力）。"""
    import xiaowo_web.evidence.pipeline as pipeline_module

    called = {"pure": 0}
    monkeypatch.setattr(
        pipeline_module.EvidencePipeline, "_pure_search_answer",
        lambda self, *a, **k: called.__setitem__("pure", called["pure"] + 1),
    )

    class SmartSearch(PureSearch):
        async def generate(self, query, *, system_prompt="", model=None, limit=8):
            return "智能生成答案", list(_REFS)

    asyncio.run(_pipeline(tmp_path, SmartSearch(), mode="smart").answer("问题"))
    assert called["pure"] == 0


def test_mode_pure_uses_pure_branch(tmp_path, patched) -> None:
    search = PureSearch()
    bundle = asyncio.run(_pipeline(tmp_path, search, mode="pure").answer("中国科学技术大学图书馆开放时间？"))
    assert search.calls, "pure 模式必须调用纯搜索"
    assert bundle.terminal_reason in {"AI_GENERATED", "EVIDENCE_INSUFFICIENT"}


# ── 对冲式闸门（2026-09-17 第三级：不整篇拒答，只标注没依据的数字） ──────────────

def test_hedge_marks_unsupported_numbers(tmp_path, monkeypatch) -> None:
    """修复后仍有残留 → 就地把无据数字换成「资料未给出」，有依据的部分照常给。"""
    import xiaowo_web.evidence.compose as compose_module

    monkeypatch.setattr(compose_module, "compose_with_own_llm",
                        lambda q, r, on_delta=None: "图书馆每周开放 9*9 小时，电话 63607647。")
    monkeypatch.setattr(compose_module, "injected_context", lambda: "")
    monkeypatch.setattr(compose_module, "repair_answer",
                        lambda q, r, prev, bad: "图书馆每周开放 9*9 小时，电话 63607647。")

    report: list[str] = []
    answer, unsupported = compose_module.compose_and_verify("图书馆开放时间？", _REFS, report=report)
    assert unsupported == [], "对冲之后不应再有无法核实的数字"
    assert "9*9" not in answer, "无据数字必须被换掉"
    assert "资料未给出" in answer
    assert "63607647" in answer, "有依据的电话必须保留"
    assert report and "没有依据" in report[0], "对冲几处要写回 report 供 limitations 使用"


def test_hedge_refuses_when_nothing_verifiable_left(tmp_path, monkeypatch) -> None:
    """答案里的数字**全是**无据的 → 数字就是它的全部内容，仍交回调用方拒答。"""
    import xiaowo_web.evidence.compose as compose_module

    monkeypatch.setattr(compose_module, "compose_with_own_llm", lambda q, r, on_delta=None: "开放 9*9 小时。")
    monkeypatch.setattr(compose_module, "injected_context", lambda: "")
    monkeypatch.setattr(compose_module, "repair_answer", lambda q, r, prev, bad: "开放 8*8 小时。")

    _answer, unsupported = compose_module.compose_and_verify("图书馆开放时间？", _REFS)
    assert unsupported, "一个可核验事实都不剩时必须把问题交给调用方"


def test_hedge_note_has_no_digits() -> None:
    """末尾说明里不能有数字 —— 否则会被自己的可核验校验当成新的无据事实。"""
    import re as _re
    import xiaowo_web.evidence.compose as compose_module

    assert not _re.search(r"\d", compose_module._HEDGE_NOTE)


def test_hedge_replaces_every_occurrence(tmp_path, monkeypatch) -> None:
    """同一个无据数字出现多次时要全部替换，否则残留仍会被判不合格。"""
    import xiaowo_web.evidence.compose as compose_module

    monkeypatch.setattr(compose_module, "compose_with_own_llm",
                        lambda q, r, on_delta=None: "开放 9*9 小时（9*9），电话 63607647。")
    monkeypatch.setattr(compose_module, "injected_context", lambda: "")
    monkeypatch.setattr(compose_module, "repair_answer", lambda q, r, prev, bad: "")

    answer, unsupported = compose_module.compose_and_verify("图书馆开放时间？", _REFS)
    assert unsupported == []
    assert "9*9" not in answer


def test_hedge_note_reaches_limitations(tmp_path, monkeypatch) -> None:
    """合成写回的对冲说明要进 limitations（用户能看到"哪几处没依据"）。"""
    import xiaowo_web.evidence.pipeline as pipeline_module

    def _fake_compose(question, refs, report=None, on_delta=None):
        if report is not None:
            report.append("答案里有 1 处具体数字/日期在检索资料中没有依据，已就地标注为「资料未给出」。")
        return "图书馆每周开放时间未在资料中给出，电话 63607647。", []

    monkeypatch.setattr(pipeline_module, "judge_relevance", lambda question, refs, **kw: True)
    monkeypatch.setattr(pipeline_module, "compose_and_verify", _fake_compose)

    bundle = asyncio.run(_pipeline(tmp_path, PureSearch()).answer("中国科学技术大学图书馆开放时间？"))
    assert bundle.terminal_reason == "AI_GENERATED"
    assert any("没有依据" in item for item in bundle.limitations)


# ── 取舍顺序（2026-09-17 晚）：纯搜索先答，公众号降为兜底 ─────────────────────

class SlowWechat:
    """公众号替身：collect 故意很慢（真实约 13s：抓 3 篇正文）。"""

    def __init__(self, *, delay: float = 5.0) -> None:
        self.delay = delay
        self.calls = 0
        self.cancelled = False

    async def collect(self, query):
        from xiaowo_web.evidence.wechat import WechatBundle
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return WechatBundle(articles=[])


def _pipeline_with_wechat(tmp_path, wechat, *, search=None):
    from tests.web.test_evidence_pipeline import FakeCrawler, FixedExtractor
    from xiaowo_web.evidence.url_security import UrlGuard
    return EvidencePipeline(
        _settings(tmp_path), search or PureSearch(), FakeCrawler({}),
        url_guard=UrlGuard(lambda _h, _p: ["8.8.8.8"]),
        extractor=FixedExtractor([]), wechat=wechat,
    )


def test_pure_answer_returns_without_waiting_for_wechat(tmp_path, monkeypatch) -> None:
    """纯搜索答得出来就立刻答，不等那 ~13s 的公众号分支（实测确认率仅 25%）。"""
    import time as _time
    import xiaowo_web.evidence.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "judge_relevance", lambda question, refs, **kw: True)
    monkeypatch.setattr(pipeline_module, "compose_and_verify",
                        lambda question, refs, **kw: ("图书馆每周开放 7*16 小时，电话 63607647。", []))
    slow = SlowWechat(delay=5.0)
    pipe = _pipeline_with_wechat(tmp_path, slow)

    async def run():
        t0 = _time.monotonic()
        bundle = await pipe.answer("中国科学技术大学图书馆开放时间？")
        return bundle, _time.monotonic() - t0

    bundle, dt = asyncio.run(run())
    assert bundle.terminal_reason == "AI_GENERATED"
    assert "7*16" in bundle.markdown
    assert dt < 3.0, f"不该等公众号（替身要 5s）：实测 {dt:.1f}s"
    assert slow.calls == 1, "公众号分支仍应被启动（只是不等它）"


def test_pure_refusal_waits_for_wechat_confirmation(tmp_path, monkeypatch) -> None:
    """纯搜索被闸门拒答时，公众号还有机会（它现在是兜底安全网）→ 必须等。"""
    import xiaowo_web.evidence.pipeline as pipeline_module
    from xiaowo_web.chat.models import AnswerBundle

    monkeypatch.setattr(pipeline_module, "judge_relevance", lambda question, refs, **kw: False)
    confirmed = AnswerBundle(markdown="公众号官方号确认的答案。",
                             terminal_reason="web_evidence_confirmed")
    waited = {"n": 0}

    async def fake_branch(question, year_anchor, on_stage):
        await asyncio.sleep(0.05)
        waited["n"] += 1
        return confirmed, [], ["公众号确认"]

    pipe = _pipeline_with_wechat(tmp_path, SlowWechat(delay=5.0))
    monkeypatch.setattr(pipe, "_wechat_branch", fake_branch)

    bundle = asyncio.run(pipe.answer("中国科学技术大学图书馆开放时间？"))
    assert bundle is confirmed, "纯搜索拒答时应采用公众号确认的答案"
    assert waited["n"] == 1


def test_pure_refusal_without_confirmation_stays_refusal(tmp_path, monkeypatch) -> None:
    """纯搜索拒答 + 公众号也没确认 → 仍是拒答（终态必须留给 runner 回退本地）。"""
    import xiaowo_web.evidence.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "judge_relevance", lambda question, refs, **kw: False)
    pipe = _pipeline_with_wechat(tmp_path, SlowWechat(delay=0.0))
    bundle = asyncio.run(pipe.answer("中国科学技术大学图书馆开放时间？"))
    assert bundle.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert bundle.sources == [], "拒答不展示无依据内容（也不补公众号来源）"
