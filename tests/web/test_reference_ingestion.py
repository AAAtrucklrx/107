"""联网引用 → 抓整页 → 入审核库（2026-09-16）。

背景：smart 路径是唯一在用的联网路径，但它在 `_smart_answer` 就 return 了，
`ingestion_candidates` 只在经典链路产生 → **联网捞到的资料从不沉淀**。
本文件锁住新增的两段接线：

1. `EvidencePipeline.fetch_candidates_for_ingestion()`：把 references 的 URL
   抓成满足 `ReviewStore.enqueue_candidate` 硬性要求（snapshot_text +
   evidence_span_hash）的候选；
2. `ChatManager._ingest_references()`：后台任务，把候选交给 ingestion_sink。
"""
from __future__ import annotations

import asyncio
import hashlib

from tests.web.helpers import make_settings
from tests.web.test_evidence_pipeline import FixedExtractor
from xiaowo_web.chat.manager import ChatManager
from xiaowo_web.evidence.pipeline import EvidencePipeline
from xiaowo_web.evidence.url_security import UrlGuard


class _Page:
    def __init__(self, url: str, markdown: str, title: str = "标题") -> None:
        self.requested_url = url
        self.final_url = url
        self.title = title
        self.markdown = markdown
        self.status_code = 200
        self.content_type = "text/html"
        self.fetched_at = "2026-09-16T00:00:00Z"
        self.published_at = None
        self.content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        self.robots_allowed = True
        self.peer_ip_verified = True


class _Crawler:
    def __init__(self, pages: dict[str, _Page | None]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    async def crawl(self, url: str):
        self.asked.append(url)
        return self.pages.get(url)

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def _pipeline(tmp_path, crawler) -> EvidencePipeline:
    return EvidencePipeline(
        make_settings(tmp_path),
        search=None,          # 本用例不触发检索
        crawler=crawler,
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([]),
    )


LONG = "正文内容" * 80


def test_fetch_candidates_meets_sink_contract(tmp_path) -> None:
    """候选必须带 snapshot_text + evidence_span_hash，否则 sink 会 ValueError。"""
    url = "https://www.teach.ustc.edu.cn/notice/1.html"
    crawler = _Crawler({url: _Page(url, LONG, "教务处通知")})
    pipes = _pipeline(tmp_path, crawler)
    out = asyncio.run(pipes.fetch_candidates_for_ingestion([url]))
    assert len(out) == 1
    cand = out[0]
    assert cand["snapshot_text"] and cand["evidence_span_hash"]
    assert cand["normalized_url"] == url
    assert cand["title"] == "教务处通知"
    assert cand["content_type"] == "text/html"
    assert cand["source_id"].startswith("ref-")


def test_fetch_candidates_skips_short_and_failed(tmp_path) -> None:
    """过短（多半抓取失败/导航页）与抓取失败一律跳过，且不影响其他条。"""
    ok = "https://www.teach.ustc.edu.cn/ok.html"
    short = "https://www.teach.ustc.edu.cn/short.html"
    fail = "https://www.teach.ustc.edu.cn/fail.html"
    crawler = _Crawler({ok: _Page(ok, LONG), short: _Page(short, "太短"), fail: None})
    out = asyncio.run(_pipeline(tmp_path, crawler).fetch_candidates_for_ingestion([ok, short, fail]))
    assert [c["normalized_url"] for c in out] == [ok]


def test_fetch_candidates_dedupes_and_caps(tmp_path) -> None:
    """同 URL 去重；超出 limit 的不抓。"""
    urls = [f"https://www.teach.ustc.edu.cn/{i}.html" for i in range(12)]
    # 各页内容不同，避免被内容指纹去重（那是另一个用例的事）
    crawler = _Crawler({u: _Page(u, LONG + u) for u in urls})
    out = asyncio.run(_pipeline(tmp_path, crawler).fetch_candidates_for_ingestion(urls, limit=3))
    assert len(out) == 3
    out2 = asyncio.run(_pipeline(tmp_path, crawler).fetch_candidates_for_ingestion([urls[0], urls[0]]))
    assert len(out2) == 1


def test_manager_ingests_references_in_background(tmp_path) -> None:
    """manager 用注入的 page_fetcher 抓整页，再交给 ingestion_sink（namespace 正确）。"""
    calls: list[tuple[str, list[dict]]] = []
    fetched: list[list[str]] = []

    class _Sink:
        def enqueue(self, namespace: str, candidates: list[dict]):
            calls.append((namespace, candidates))
            return []

    async def _fetcher(urls: list[str]):
        fetched.append(list(urls))
        return [{"snapshot_text": "正文", "evidence_span_hash": "h"}]

    manager = ChatManager(
        make_settings(tmp_path),
        store=object(),      # type: ignore[arg-type]
        runner=object(),     # type: ignore[arg-type]
        ingestion_sink=_Sink(),
        page_fetcher=_fetcher,
    )
    asyncio.run(manager._ingest_references("demo", ["https://a.example/1"]))
    assert fetched == [["https://a.example/1"]]
    assert calls and calls[0][0] == "demo"
    assert calls[0][1][0]["snapshot_text"] == "正文"


def test_manager_ingest_failure_is_swallowed(tmp_path) -> None:
    """后台补料失败绝不能冒泡（回答已经返回给用户了）。"""
    async def _boom(_urls):
        raise RuntimeError("crawl down")

    manager = ChatManager(
        make_settings(tmp_path), store=object(), runner=object(),  # type: ignore[arg-type]
        ingestion_sink=object(), page_fetcher=_boom,
    )
    asyncio.run(manager._ingest_references("demo", ["https://a.example/1"]))


def test_fetch_candidates_dedupes_identical_content(tmp_path) -> None:
    """同一份内容被多个子域返回时只留一条（实测天气页 6 个子域同内容）。"""
    a = "https://baidu.weather.com.cn/m/101220101.shtml"
    b = "https://wap.weather.com.cn/m/101220101.shtml"
    c = "https://uc.weather.com.cn/m/101220101.shtml"
    other = "https://www.teach.ustc.edu.cn/x.html"
    crawler = _Crawler({a: _Page(a, LONG), b: _Page(b, LONG), c: _Page(c, LONG),
                        other: _Page(other, LONG + "不同内容")})
    out = asyncio.run(_pipeline(tmp_path, crawler).fetch_candidates_for_ingestion([a, b, c, other]))
    assert len(out) == 2, [x["normalized_url"] for x in out]
