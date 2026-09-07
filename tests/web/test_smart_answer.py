"""百度智能搜索生成直达答复（web_answer_mode=smart）——提示词工程、来源标注与失败回退。"""

from __future__ import annotations

import asyncio

from tests.web.helpers import make_settings
from tests.web.test_evidence_pipeline import FakeCrawler, FixedExtractor
from xiaowo_web.evidence.models import CrawledPage, SearchBatch
from xiaowo_web.evidence.pipeline import EvidencePipeline
from xiaowo_web.evidence.url_security import UrlGuard


def _smart_settings(tmp_path, *, mode: str = "smart") -> object:
    return make_settings(
        tmp_path,
        extra={
            "XIAOWO_SEARCH_PROVIDER": "baidu",
            "XIAOWO_BAIDU_SEARCH_KEY": "bce-v3/test-key",
            "XIAOWO_WEB_ANSWER_MODE": mode,
        },
    )


class SmartSearch:
    """带 generate() 的搜索客户端替身：smart 成功/失败可控。"""

    def __init__(self, *, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    async def generate(self, query: str, *, system_prompt: str = "", model: str | None = None, limit: int = 8) -> str:
        self.calls.append({"query": query, "system_prompt": system_prompt, "model": model, "limit": limit})
        if self.error is not None:
            raise self.error
        return self.text or "答复"

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        return SearchBatch([])

    async def close(self) -> None:
        return None


def _pipeline(tmp_path, search, *, mode: str = "smart") -> EvidencePipeline:
    return EvidencePipeline(
        _smart_settings(tmp_path, mode=mode),
        search,
        FakeCrawler({}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([]),
    )


def test_smart_answer_returns_generated_text(tmp_path) -> None:
    text = "2026年中秋节放假时间为9月25日至9月27日，共3天。"
    search = SmartSearch(text=text)
    bundle = asyncio.run(_pipeline(tmp_path, search).answer("2026年中秋节放假安排"))

    assert bundle.markdown == text
    assert bundle.terminal_reason == "AI_GENERATED"
    assert bundle.claims[0]["status"] == "generated"
    assert bundle.claims[0]["text"] == text
    # 来源徽标：AI 搜索生成（无引用），未附引用必须出现在限制里
    assert any(s.get("source_id") == "s-ai-search" for s in bundle.sources)
    assert bundle.sources[0]["display_url"] is None
    assert any("未附独立引用" in item for item in bundle.limitations)
    # 提示词工程已送达
    call = search.calls[0]
    assert call["query"] == "2026年中秋节放假安排"
    assert "结论先行" in call["system_prompt"]
    assert call["model"] == "ernie-4.5-turbo-128k"


def test_smart_prompt_includes_campus_rule(tmp_path) -> None:
    search = SmartSearch(text="a")
    asyncio.run(_pipeline(tmp_path, search).answer("中科大国庆放假安排"))
    assert "学校官方口径" in search.calls[0]["system_prompt"]


def test_smart_failure_falls_back_to_classic(tmp_path) -> None:
    search = SmartSearch(error=RuntimeError("429"))
    bundle = asyncio.run(_pipeline(tmp_path, search).answer("2026年中秋节放假安排"))

    assert bundle.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert any("已回退通用检索" in item for item in bundle.limitations)


def test_classic_mode_skips_smart(tmp_path) -> None:
    search = SmartSearch(text="不应出现")
    bundle = asyncio.run(_pipeline(tmp_path, search, mode="classic").answer("2026年中秋节放假安排"))

    assert bundle.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert search.calls == []
