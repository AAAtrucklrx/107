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


def _pipeline(tmp_path, crawler, search=None) -> EvidencePipeline:
    return EvidencePipeline(
        make_settings(tmp_path),
        search=search,        # 默认 None：不触发检索
        crawler=crawler,
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([]),
    )


class _Search:
    """假百度搜索客户端：只实现纯搜索 references_only（2026-09-17 新增能力）。"""

    def __init__(self, refs: list[dict] | None = None) -> None:
        self.refs = refs or []
        self.calls: list[tuple[str, str]] = []

    async def references_only(self, query: str, *, site: str = "", limit: int = 8):
        self.calls.append((query, site))
        return self.refs

    async def close(self) -> None:
        return None


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


def test_rich_snippet_prefers_pure_search(tmp_path) -> None:
    """抓不到正文时优先用**纯搜索模式**取更肥的原文摘录（203 字 → 1000+ 字）。

    实测：同一 endpoint 去掉 model 后 content 从固定 203 字变 1000~1499 字、耗时 0.5~1s，
    且与真实网页做 12-gram 比对包含率 84~99%（逐字摘录，不是模型改写）。
    """
    url = "https://mp.weixin.qq.com/s?__biz=abc&mid=9&idx=1"
    crawler = _Crawler({url: None})
    short = "微信文章摘要，只有两百字出头。" * 12          # 智能模式给的短摘要
    rich = "中国科学技术大学图书馆开放时间：东区、西区、高新区馆舍每周 7*16 小时。" * 20
    search = _Search([{"url": url, "title": "图书馆开放时间", "content": rich}])
    out = asyncio.run(
        _pipeline(tmp_path, crawler, search).fetch_candidates_for_ingestion(
            [url], snippets={url: {"content": short, "title": "图书馆开放时间"}}
        )
    )
    assert len(out) == 1
    cand = out[0]
    assert cand["content_type"] == "text/search-snippet"
    assert cand["snapshot_text"] == rich, "应当用纯搜索的更长摘录，而不是智能模式的短摘要"
    assert f"仅搜索摘要 {len(rich)} 字" in cand["title"]
    # 查询词用页面自身标题（不是用户问题），并带站点过滤
    assert search.calls == [("图书馆开放时间", "mp.weixin.qq.com")]


def test_rich_snippet_never_substitutes_another_page(tmp_path) -> None:
    """同站但 URL 不同的返回**绝不能**顶替目标页——宁可退回短摘要。"""
    url = "https://mp.weixin.qq.com/s?__biz=abc&mid=9&idx=1"
    crawler = _Crawler({url: None})
    short = "目标页自己的摘要内容。" * 20
    search = _Search([{"url": "https://mp.weixin.qq.com/s?__biz=OTHER", "content": "别的页面" * 100}])
    out = asyncio.run(
        _pipeline(tmp_path, crawler, search).fetch_candidates_for_ingestion(
            [url], snippets={url: {"content": short, "title": "标题"}}
        )
    )
    assert len(out) == 1
    assert out[0]["snapshot_text"] == short


def test_navigation_page_snippet_is_dropped(tmp_path) -> None:
    """导航/列表页摘要（实测 66/77/28 字，"公告 公告 公告 …"）必须被门槛挡掉。"""
    url = "https://lib.ustc.edu.cn/2008/"
    crawler = _Crawler({url: None})
    nav = "公告 公告 公告 图书馆2023寒假开放安排 公告 图书馆2022暑期开放安排 公告 公告"
    out = asyncio.run(
        _pipeline(tmp_path, crawler, _Search([])).fetch_candidates_for_ingestion(
            [url], snippets={url: {"content": nav, "title": "公告列表"}}
        )
    )
    assert out == [], "导航页不该进审核库"


def test_snippet_docs_skip_llm_cleaner(tmp_path) -> None:
    """摘要类文档不走 LLM 清洗的「不超过原文 60%」——那对 1000 字摘录是二次伤害。"""
    from xiaowo_web.review import ReviewStore
    from xiaowo_web.worker import IngestionWorker

    class _SpyCleaner:
        called = 0

        def clean(self, snapshot_text, metadata):
            _SpyCleaner.called += 1
            from xiaowo_web.worker.ingestion import CleanDraft
            return CleanDraft(title="被压缩", scope="general", category="stable_general",
                              content="压缩后的短文本", chunks=["压缩后的短文本"])

    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    body = "中国科学技术大学图书馆开放时间与研讨室预约方式。" * 40
    store.enqueue_candidate("demo", {
        "source_id": "s-snip", "normalized_url": "https://lib.ustc.edu.cn/a",
        "final_url": "https://lib.ustc.edu.cn/a", "title": "图书馆开放时间",
        "institution": "中国科学技术大学", "level": "official_primary",
        "fetched_at": "2026-09-17T00:00:00Z", "content_type": "text/search-snippet",
        "snapshot_text": body, "evidence_span_hash": "span-snip",
    })
    worker = IngestionWorker(store, cleaner=_SpyCleaner(), worker_id="w-snip")
    assert worker.run_once() == "done"
    assert _SpyCleaner.called == 0, "摘要类文档不该调用 LLM 清洗"
    item = store.list_items("demo")[0]
    detail = store.get_item("demo", item["item_id"])
    joined = "".join(c["content_text"] for c in detail["chunks"])
    assert len(joined) >= len(body) * 0.95, f"摘要被压缩了：{len(body)} → {len(joined)}"


def test_manager_ingests_references_in_background(tmp_path) -> None:
    """manager 用注入的 page_fetcher 抓整页，再交给 ingestion_sink（namespace 正确）。"""
    calls: list[tuple[str, list[dict]]] = []
    fetched: list[list[str]] = []

    class _Sink:
        def enqueue(self, namespace: str, candidates: list[dict]):
            calls.append((namespace, candidates))
            return []

    async def _fetcher(urls: list[str], snippets=None, query=""):
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
    async def _boom(_urls, snippets=None, query=""):
        raise RuntimeError("crawl down")

    manager = ChatManager(
        make_settings(tmp_path), store=object(), runner=object(),  # type: ignore[arg-type]
        ingestion_sink=object(), page_fetcher=_boom,
    )
    asyncio.run(manager._ingest_references("demo", ["https://a.example/1"]))


def test_fetch_candidates_falls_back_to_search_snippet(tmp_path) -> None:
    """robots 禁抓的站点（实测 mp.weixin.qq.com 是 `Disallow: /`）**不绕 robots**：
    抓不到正文时改用检索器返回的摘要，并**显式标注仅摘要**，绝不冒充整页。"""
    url = "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1"
    crawler = _Crawler({url: None})          # 抓取失败（适配器会归一化成 502）
    # 长度需 >= _SNIPPET_MIN_CHARS(200)：太短的（导航/列表页）会被门槛挡掉
    snippet = "中国科学技术大学转专业政策：二年级可全校申请，三年级只能本院内转。" * 8
    out = asyncio.run(_pipeline(tmp_path, crawler).fetch_candidates_for_ingestion(
        [url], snippets={url: {"content": snippet, "title": "转专业政策解读"}}))
    assert len(out) == 1
    cand = out[0]
    assert cand["content_type"] == "text/search-snippet"
    assert "仅搜索摘要" in cand["title"] and "转专业政策解读" in cand["title"]
    assert cand["snapshot_text"] == snippet
    assert cand["normalized_url"] == url


def test_no_snippet_fallback_for_unsafe_or_too_short(tmp_path) -> None:
    """① URL 校验不过（内网）**绝不**走摘要兜底；② 摘要过短也不入库。"""
    unsafe = "http://127.0.0.1/secret"
    short = "https://www.teach.ustc.edu.cn/s.html"
    crawler = _Crawler({unsafe: None, short: None})
    pipes = _pipeline(tmp_path, crawler)
    snips = {unsafe: {"content": "敏感内容" * 40, "title": "t"},
             short: {"content": "太短", "title": "t"}}
    out = asyncio.run(pipes.fetch_candidates_for_ingestion([unsafe, short], snippets=snips))
    assert out == [], [c["normalized_url"] for c in out]
    assert unsafe not in crawler.asked, "内网 URL 不该被送去抓取"


def test_manager_passes_snippets_to_fetcher(tmp_path) -> None:
    """摘要必须一路传到 page_fetcher，否则兜底形同虚设。"""
    seen: list[object] = []

    async def _fetcher(urls, snippets=None):
        seen.append(snippets)
        return []

    manager = ChatManager(
        make_settings(tmp_path), store=object(), runner=object(),  # type: ignore[arg-type]
        ingestion_sink=object(), page_fetcher=_fetcher,
    )
    snips = {"https://a.example/1": {"content": "x" * 100, "title": "t"}}
    asyncio.run(manager._ingest_references("demo", ["https://a.example/1"], snips))
    assert seen == [snips]


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
