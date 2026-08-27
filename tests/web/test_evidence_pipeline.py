"""Pipeline-level confirmation, insufficiency, and redirect safety."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

from tests.web.helpers import make_settings
from xiaowo_web.evidence.models import (
    CrawledPage,
    ExtractedClaim,
    ExtractedEvidence,
    SearchBatch,
    SearchHit,
)
from xiaowo_web.evidence.pipeline import EvidencePipeline
from xiaowo_web.evidence.url_security import UrlGuard


class FakeSearch:
    def __init__(self, hits: list[SearchHit], partial: bool = False) -> None:
        self.hits = hits
        self.partial = partial
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        self.queries.append(query)
        return SearchBatch(self.hits[:limit], partial=self.partial)

    async def close(self) -> None:
        return None


class FakeCrawler:
    def __init__(self, pages: dict[str, CrawledPage], healthy: bool = True) -> None:
        self.pages = pages
        self.healthy = healthy

    async def health(self) -> bool:
        return self.healthy

    async def crawl(self, url: str) -> CrawledPage:
        return self.pages[url]

    async def close(self) -> None:
        return None


class FixedExtractor:
    def __init__(self, claims: list[ExtractedClaim]) -> None:
        self.claims = claims

    async def extract(self, _question: str, _pages) -> list[ExtractedClaim]:
        return self.claims


def _page(url: str, markdown: str, *, final_url: str | None = None) -> CrawledPage:
    return CrawledPage(
        requested_url=url,
        final_url=final_url or url,
        title="教务处公告",
        markdown=markdown,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(UTC).isoformat(),
        published_at="2026-08-27",
        content_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        robots_allowed=True,
        peer_ip_verified=True,
    )


def test_official_quote_can_confirm_a_claim(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/1"
    markdown = "教务处公告明确说明，本事项自二零二六年九月一日起执行。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([SearchHit("公告", url)]),
        FakeCrawler({url: _page(url, markdown)}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="本事项自二零二六年九月一日起执行。",
            evidence=(ExtractedEvidence(
                source_id=source_id,
                relation="supports",
                quote=markdown,
            ),),
        )]),
    )

    answer = asyncio.run(pipeline.answer("这项公开事项何时执行"))
    assert answer.claims[0]["status"] == "confirmed"
    assert answer.terminal_reason == "web_evidence_confirmed"
    assert "[1]" in answer.markdown
    assert answer.sources[0]["level"] == "official_primary"
    assert answer.sources[0]["citation"] == 1
    assert len(answer.ingestion_candidates) == 1
    candidate = answer.ingestion_candidates[0]
    assert candidate["snapshot_text"] == markdown
    assert "question" not in candidate


def test_found_general_source_stays_insufficient_and_is_listed(tmp_path) -> None:
    url = "https://example.com/article"
    markdown = "一般网页中的公开背景材料，不能单独形成确定结论。"
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([SearchHit("一般来源", url)], partial=True),
        FakeCrawler({url: _page(url, markdown)}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
    )
    answer = asyncio.run(pipeline.answer("查询公开背景信息"))
    assert answer.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert answer.markdown == "暂未找到足够可靠的联网证据。"
    assert len(answer.sources) == 1
    assert answer.sources[0]["level"] == "general"
    assert any("部分搜索引擎" in item for item in answer.limitations)


def test_private_redirect_target_is_discarded(tmp_path) -> None:
    url = "https://example.com/article"
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([SearchHit("重定向", url)]),
        FakeCrawler({url: _page(url, "公开页面正文足够长。", final_url="http://127.0.0.1/admin")}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
    )
    answer = asyncio.run(pipeline.answer("查询公开重定向信息"))
    assert answer.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert answer.sources == []
    assert any("重定向" in item for item in answer.limitations)


def test_sensitive_query_never_reaches_search(tmp_path) -> None:
    search = FakeSearch([])
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        search,
        FakeCrawler({}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
    )
    answer = asyncio.run(pipeline.answer("帮我查我的成绩"))
    assert answer.terminal_reason == "PERSONAL_QUERY"
    assert search.queries == []
