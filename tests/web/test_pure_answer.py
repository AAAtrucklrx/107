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
    monkeypatch.setattr(pipeline_module, "compose_and_verify",
                        lambda question, refs: (state["answer"], list(state["unsupported"])))
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
    assert "不足以支撑一份可靠回答" in bundle.markdown
    assert any("无法核实" in item for item in bundle.limitations)


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
