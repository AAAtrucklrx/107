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
from xiaowo_web.evidence.rewrite import QueryRewriter
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


class EmptyThenHitSearch:
    def __init__(self, second_hits: list[SearchHit]) -> None:
        self.second_hits = second_hits
        self.queries: list[str] = []
        self.calls = 0

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        self.queries.append(query)
        self.calls += 1
        if self.calls == 1:
            return SearchBatch([])
        return SearchBatch(self.second_hits[:limit])

    async def close(self) -> None:
        return None


class FakeCrawler:
    def __init__(self, pages: dict[str, CrawledPage], healthy: bool = True) -> None:
        self.pages = pages
        self.healthy = healthy
        self.crawled: list[str] = []

    async def health(self) -> bool:
        return self.healthy

    async def crawl(self, url: str) -> CrawledPage:
        self.crawled.append(url)
        return self.pages[url]

    async def close(self) -> None:
        return None


class FixedExtractor:
    def __init__(self, claims: list[ExtractedClaim]) -> None:
        self.claims = claims

    async def extract(self, _question: str, _pages) -> list[ExtractedClaim]:
        return self.claims


def _page(url: str, markdown: str, *, final_url: str | None = None, published_at: str = "2026-08-27") -> CrawledPage:
    return CrawledPage(
        requested_url=url,
        final_url=final_url or url,
        title="教务处公告",
        markdown=markdown,
        status_code=200,
        content_type="text/html",
        fetched_at=datetime.now(UTC).isoformat(),
        published_at=published_at,
        content_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        robots_allowed=True,
        peer_ip_verified=True,
    )


class QueryAwareSearch:
    def __init__(self, hits_by_query: dict[str, list[SearchHit]]) -> None:
        self.hits_by_query = hits_by_query
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        self.queries.append(query)
        return SearchBatch((self.hits_by_query.get(query) or [])[:limit])

    async def close(self) -> None:
        return None


class ScriptedRewriter:
    def __init__(self, results: list[list[str] | None]) -> None:
        self.results = list(results)
        self.calls = 0
        self.hints: list[bool] = []

    async def rewrite(self, _question: str, *, short_hint: bool = False) -> list[str] | None:
        self.calls += 1
        self.hints.append(short_hint)
        if self.results:
            return self.results.pop(0)
        return None


def test_rewritten_query_is_used_for_search(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/notice-teaching/19339.html"
    markdown = "教务处公告明确说明，秋季学期选课通知的具体发布时间为九月一日。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    search = QueryAwareSearch({"科大 教务处 选课通知": [SearchHit("公告", url)]})
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        search,
        FakeCrawler({url: _page(url, markdown)}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="秋季学期选课通知的具体发布时间为九月一日。",
            evidence=(ExtractedEvidence(
                source_id=source_id,
                relation="supports",
                quote=markdown,
            ),),
        )]),
        rewriter=ScriptedRewriter([["科大 教务处 选课通知"]]),
    )

    answer = asyncio.run(pipeline.answer("请问中国科学技术大学2026年秋季学期本科生选课通知的具体发布时间？"))
    assert search.queries[0] == "科大 教务处 选课通知"
    assert answer.terminal_reason == "web_evidence_confirmed"


def test_second_round_searches_different_query_after_empty_first(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/notice-teaching/20425.html"
    markdown = "教务处公告说明，选课通知已发布于教务处教学子栏目。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    search = QueryAwareSearch({"换词C": [SearchHit("公告", url)]})
    rewriter = ScriptedRewriter([["关键词A"], ["换词C"]])
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        search,
        FakeCrawler({url: _page(url, markdown)}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="选课通知已发布于教务处教学子栏目。",
            evidence=(ExtractedEvidence(
                source_id=source_id,
                relation="supports",
                quote=markdown,
            ),),
        )]),
        rewriter=rewriter,
    )

    answer = asyncio.run(pipeline.answer("请问中国科学技术大学2026年秋季庆典安排在什么时候？"))
    # 第一轮：关键词A（空）+ 退避重试一次；加轮提示改写得到 换词C 后再搜一次并确认。
    assert search.queries == ["关键词A", "关键词A", "换词C"]
    assert rewriter.hints == [False, True]
    assert answer.terminal_reason == "web_evidence_confirmed"


def test_official_site_query_is_used_on_second_round(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/notice-teaching/20425.html"
    markdown = "教务处公告说明，选课通知已发布于教务处教学子栏目。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    site_query = "site:ustc.edu.cn 2026 选课"
    search = QueryAwareSearch({site_query: [SearchHit("公告", url)]})
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        search,
        FakeCrawler({url: _page(url, markdown)}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="选课通知已发布于教务处教学子栏目。",
            evidence=(ExtractedEvidence(
                source_id=source_id,
                relation="supports",
                quote=markdown,
            ),),
        )]),
        rewriter=ScriptedRewriter([["关键词A"]]),
    )

    answer = asyncio.run(pipeline.answer("请问中国科学技术大学2026年秋季学期本科生选课通知在哪里发布？"))
    assert search.queries == ["关键词A", "关键词A", site_query]
    assert answer.terminal_reason == "web_evidence_confirmed"


def test_empty_first_search_retries_once(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/2"
    markdown = "教务处公告说明，本事项自二零二六年十月一日起执行。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    search = EmptyThenHitSearch([SearchHit("公告", url)])
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        search,
        FakeCrawler({url: _page(url, markdown)}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="本事项自二零二六年十月一日起执行。",
            evidence=(ExtractedEvidence(
                source_id=source_id,
                relation="supports",
                quote=markdown,
            ),),
        )]),
    )

    answer = asyncio.run(pipeline.answer("这项公开事项何时执行"))
    assert search.calls == 2
    assert answer.terminal_reason == "web_evidence_confirmed"


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
        extractor=FixedExtractor([]),
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
        extractor=FixedExtractor([]),
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
        extractor=FixedExtractor([]),
    )
    answer = asyncio.run(pipeline.answer("帮我查我的成绩"))
    assert answer.terminal_reason == "PERSONAL_QUERY"
    assert search.queries == []


def test_year_mismatch_evidence_is_excluded(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/notice-teaching/19339.html"
    markdown = "教务处公告明确说明，秋季学期选课通知发布于教学子栏目。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([SearchHit("公告", url)]),
        FakeCrawler({url: _page(url, markdown, published_at="2025-07-01")}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="秋季学期选课通知发布于教学子栏目。",
            evidence=(ExtractedEvidence(source_id=source_id, relation="supports", quote=markdown),),
        )]),
        rewriter=ScriptedRewriter([["选课通知 2026"]]),
    )

    answer = asyncio.run(pipeline.answer("请问中国科学技术大学2026年秋季学期本科生选课通知在哪里发布？"))
    assert answer.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert any("发布年份与问题年份不一致" in item for item in answer.limitations)


def test_matching_year_keeps_claim_confirmed(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/notice-teaching/20425.html"
    markdown = "教务处公告明确说明，秋季学期选课通知发布于教学子栏目。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([SearchHit("公告", url)]),
        FakeCrawler({url: _page(url, markdown, published_at="2026-07-16")}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="秋季学期选课通知发布于教学子栏目。",
            evidence=(ExtractedEvidence(source_id=source_id, relation="supports", quote=markdown),),
        )]),
        rewriter=ScriptedRewriter([["选课通知 2026"]]),
    )

    answer = asyncio.run(pipeline.answer("请问中国科学技术大学2026年秋季学期本科生选课通知在哪里发布？"))
    assert answer.terminal_reason == "web_evidence_confirmed"
    assert answer.claims[0]["status"] == "confirmed"


def test_year_mismatch_evidence_is_excluded(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/notice-teaching/19339.html"
    markdown = "教务处公告明确说明，秋季学期选课通知发布于教学子栏目。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([SearchHit("公告", url)]),
        FakeCrawler({url: _page(url, markdown, published_at="2025-07-01")}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="秋季学期选课通知发布于教学子栏目。",
            evidence=(ExtractedEvidence(source_id=source_id, relation="supports", quote=markdown),),
        )]),
        rewriter=ScriptedRewriter([["选课通知 2026"]]),
    )

    answer = asyncio.run(pipeline.answer("请问中国科学技术大学2026年秋季学期本科生选课通知在哪里发布？"))
    assert answer.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert any("发布年份与问题年份不一致" in item for item in answer.limitations)


def test_matching_year_keeps_claim_confirmed(tmp_path) -> None:
    url = "https://www.teach.ustc.edu.cn/notice/notice-teaching/20425.html"
    markdown = "教务处公告明确说明，秋季学期选课通知发布于教学子栏目。"
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([SearchHit("公告", url)]),
        FakeCrawler({url: _page(url, markdown, published_at="2026-07-16")}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([ExtractedClaim(
            text="秋季学期选课通知发布于教学子栏目。",
            evidence=(ExtractedEvidence(source_id=source_id, relation="supports", quote=markdown),),
        )]),
        rewriter=ScriptedRewriter([["选课通知 2026"]]),
    )

    answer = asyncio.run(pipeline.answer("请问中国科学技术大学2026年秋季学期本科生选课通知在哪里发布？"))
    assert answer.terminal_reason == "web_evidence_confirmed"
    assert answer.claims[0]["status"] == "confirmed"


def test_hits_are_reranked_semantically_before_crawl(tmp_path, monkeypatch) -> None:
    """搜索命中按语义精排后再抓取（trust 级内按 rerank 相关分取前 3）。"""
    urls = [f"https://example.com/news/{name}.html" for name in ("a", "b", "c", "d")]
    hits = [SearchHit(f"标题{i}-{name}", url) for i, (name, url) in enumerate(zip(("a", "b", "c", "d"), urls))]
    pages = {url: _page(url, f"第 {name} 篇文章正文，与前文无关。") for name, url in zip(("a", "b", "c", "d"), urls)}
    calls: dict = {}

    def fake_rerank(query: str, docs: list[str], top_k: int) -> list[int]:
        calls["query"] = query
        calls["docs"] = list(docs)
        assert len(docs) == 4
        return list(range(top_k - 1, -1, -1))  # 倒序：原第 4 条最相关

    monkeypatch.setattr("knowledge.reranker.rerank", fake_rerank)
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch(hits),
        FakeCrawler(pages),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([]),
        rewriter=ScriptedRewriter([["关键词A"]]),
    )

    asyncio.run(pipeline.answer("2026年秋季学期开学时间是几月几号？"))
    # 倒序重排后 top3 = 原 [d, c, b]（原 a 被语义精排挤出抓取预算）
    assert calls["docs"] == [f"标题{i}-{name}\n{hit.snippet}" for i, (name, hit) in enumerate(zip(("a", "b", "c", "d"), hits))]
    assert pipeline.crawler.crawled[:3] == [urls[3], urls[2], urls[1]]


def test_hits_retain_original_order_when_rerank_unavailable(tmp_path) -> None:
    """语义精排不可用时回退原序（trust 级内按 URL 顺序），不影响现有行为。"""
    urls = [f"https://example.com/news/{name}.html" for name in ("a", "b", "c", "d")]
    hits = [SearchHit(f"标题{i}", url) for i, url in enumerate(urls)]
    pages = {url: _page(url, f"第 {name} 篇文章正文。") for name, url in zip(("a", "b", "c", "d"), urls)}
    pipeline = EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch(hits),
        FakeCrawler(pages),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([]),
        rewriter=ScriptedRewriter([["关键词A"]]),
    )

    asyncio.run(pipeline.answer("2026年秋季学期开学时间是几月几号？"))
    assert pipeline.crawler.crawled[:3] == urls[:3]
