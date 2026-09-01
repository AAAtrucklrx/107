"""Unit tests for the WeChat retrieval channel."""

from __future__ import annotations

import asyncio
import hashlib
import sys

import httpx
import pytest

from tests.web.test_evidence_pipeline import FixedExtractor
from xiaowo_web.evidence.wechat import (
    WechatArticle, WechatClient,
    WechatClient,
    article_content_hash,
    build_markdown,
    extract_pure,
    is_official_account,
)

SEARCH_HTML = """
<html><body>
<li><div class="txt-box">
<h3><a href="/link?url=dn9a_abc123" uigs="article_title_0">中国科学技术大学关于2026年秋季学期选课的通知</a></h3>
<p class="txt-info" id="sogou_vr_0_summary_0">摘要：选课通知已发布。</p>
<div class="s-p"><span class="all-time-y2">中国科学技术大学</span></div>
</div></li>
<li><div class="txt-box">
<h3><a href="/link?url=dn9a_xyz789" uigs="article_title_1">科大少年班录取名单分析</a></h3>
<div class="s-p"><span class="all-time-y2">蜗壳小道消息</span></div>
</div></li>
<li><div class="txt-box">
<h3><a href="/link?url=dn9a_other22" uigs="article_title_2">某辅导机构招生广告</a></h3>
<div class="s-p"><span class="all-time-y2">E考博</span></div>
</div></li>
</body></html>
"""

RESOLVE_HTML = """
<meta content="always" name="referrer">
<script>
    (new Image()).src = 'https://weixin.sogou.com/approve?uuid=x&token=y&from=inner';
    setTimeout(function () {
        var url = '';
        url += 'https://mp.';
        url += 'weixin.qq.c';
        url += 'om/s?src=11&timestamp=1788275805&ver=6940&signature=TEST';
        window.location.replace(url)
    },100);
</script>
"""

ARTICLE_HTML = """
<html><head>
<meta property="og:title" content="中国科学技术大学关于2026年秋季学期选课的通知">
<meta property="og:article:author" content="中国科学技术大学">
</head><body>
<div id="js_content">教务处公告明确说明，选课通知已发布于教务处教学子栏目，请按时完成。</div>
<img data-src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/AAAA/0">
</body></html>
"""

TABLE_ARTICLE_HTML = ARTICLE_HTML + """
<img data-src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/BBBB/0">
"""


def _run(coro):
    return asyncio.run(coro)


def test_extract_pure_fields() -> None:
    pure = extract_pure(ARTICLE_HTML)
    assert pure["title"] == "中国科学技术大学关于2026年秋季学期选课的通知"
    assert pure["author"] == "中国科学技术大学"
    assert "教务处公告明确说明" in pure["text"]
    assert len(pure["images"]) == 1
    assert pure["images"][0].endswith("AAAA/0")


def test_official_account_whitelist() -> None:
    assert is_official_account("中国科学技术大学") is True
    assert is_official_account("蜗壳小道消息") is True
    assert is_official_account("中科大官方") is True
    assert is_official_account("E考博") is False
    assert is_official_account("科大讯飞") is False
    assert is_official_account("") is False


def test_build_markdown_and_hash() -> None:
    md = build_markdown("正文", ["[图1·OCR] 名单表格"])
    assert "[图1·OCR]" in md
    article = WechatArticle(title="t", author="a", url="https://mp.weixin.qq.com/s?x", markdown="正文")
    assert article_content_hash(article) != article_content_hash(
        WechatArticle(title="t", author="a", url="https://mp.weixin.qq.com/s?x", markdown="其它")
    )


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "weixin.sogou.com" and path.startswith("/link"):
            return httpx.Response(200, text=RESOLVE_HTML)
        if host == "weixin.sogou.com":
            return httpx.Response(200, text=SEARCH_HTML)
        if host == "mp.weixin.qq.com":
            return httpx.Response(200, text=TABLE_ARTICLE_HTML)
        if "llm" in host:
            body = request.content.decode("utf-8", errors="replace")
            if "BBBB" in body:
                return httpx.Response(200, json={"choices": [{"message": {"content": "姓名 性别 省份 学校名称 巴鹏程 男 山东省 高密市第一中学 蔡博轩 男 江苏省 扬州中学"}}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_collect_full_flow_with_ocr_filter(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = WechatClient(client=httpx.AsyncClient(transport=_transport()), sogou_throttle=0, article_throttle=0, ocr_throttle=0)
    bundle = _run(client.collect("中国科学技术大学 选课通知"))
    _run(client.close())
    assert len(bundle.articles) >= 1
    article = bundle.articles[0]
    assert article.title == "中国科学技术大学关于2026年秋季学期选课的通知"
    assert article.author == "中国科学技术大学"
    assert article.url.startswith("https://mp.weixin.qq.com/")
    # 图1（AAA，OCR 只回"你好"）被丢弃；图2（BBBB，表格）保留
    assert any("巴鹏程" in span for span in article.ocr_spans)


def test_blocked_search_returns_blocked_bundle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>antispider verify 请验证</html>")

    client = WechatClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sogou_throttle=0, article_throttle=0, ocr_throttle=0)
    bundle = _run(client.collect("test"))
    _run(client.close())
    assert bundle.blocked is True
    assert bundle.articles == []


def test_circuit_breaker_opens_after_repeated_blocks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="antispider verify")

    client = WechatClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        block_threshold=2, block_cooldown=60, sogou_throttle=0, article_throttle=0, ocr_throttle=0,
    )
    _run(client.collect("a"))
    _run(client.collect("b"))
    assert client.circuit_open is True
    # 熔断期间直接返回 blocked，不再请求
    bundle = _run(client.collect("c"))
    _run(client.close())
    assert bundle.blocked is True


def test_ssrf_jump_target_is_rejected() -> None:
    real_url = "http://169.254.169.254/latest/meta-data/"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "weixin.sogou.com" and request.url.path.startswith("/link"):
            return httpx.Response(200, text="<script>var url = ''; url += 'http://169.' ; url += '254.169.254/latest/meta-data/';</script>")
        if request.url.host == "weixin.sogou.com":
            return httpx.Response(200, text=SEARCH_HTML)
        return httpx.Response(404)

    client = WechatClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sogou_throttle=0, article_throttle=0, ocr_throttle=0)
    bundle = _run(client.collect("test"))
    _run(client.close())
    assert bundle.articles == []
    assert "domain not allowed" in (client.last_error or "")


def test_resolve_without_fragments_skips_article() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "weixin.sogou.com" and request.url.path.startswith("/link"):
            return httpx.Response(200, text="<html>无JS片段</html>")
        return httpx.Response(200, text=SEARCH_HTML)

    client = WechatClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sogou_throttle=0, article_throttle=0, ocr_throttle=0)
    bundle = _run(client.collect("test"))
    _run(client.close())
    assert bundle.articles == []
    assert bundle.partial is True


class FakeWechat:
    def __init__(self, articles, *, blocked=False):
        self.articles = articles
        self.blocked = blocked
        self.calls = 0

    async def collect(self, _query):
        self.calls += 1
        from xiaowo_web.evidence.wechat import WechatBundle
        return WechatBundle(self.articles, blocked=self.blocked)

    async def close(self):
        return None


async def _collect_article(title: str, author: str, markdown: str, url: str) -> WechatArticle:
    return WechatArticle(title=title, author=author, url=url, markdown=markdown)


def _wechat_pipeline(tmp_path, wechat, question, extractor):
    from tests.web.helpers import make_settings
    from xiaowo_web.evidence.pipeline import EvidencePipeline
    from xiaowo_web.evidence.url_security import UrlGuard
    from tests.web.test_evidence_pipeline import FakeCrawler, FakeSearch, _page
    import hashlib as _hash
    from xiaowo_web.evidence.models import ExtractedClaim, ExtractedEvidence, SearchHit

    url = "https://mp.weixin.qq.com/s?src=11&signature=TEST"
    return EvidencePipeline(
        make_settings(tmp_path),
        FakeSearch([]),
        FakeCrawler({}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=extractor,
        wechat=wechat,
    )


def test_wechat_official_article_can_confirm_claim(tmp_path) -> None:
    from tests.web.helpers import make_settings
    from xiaowo_web.evidence.models import ExtractedClaim, ExtractedEvidence
    from xiaowo_web.evidence.pipeline import EvidencePipeline
    from xiaowo_web.evidence.url_security import UrlGuard
    from tests.web.test_evidence_pipeline import FakeCrawler, FakeSearch

    url = "https://mp.weixin.qq.com/s?src=11&signature=T1"
    markdown = "教务处公告明确说明，秋季学期选课通知发布于教学子栏目。"
    article = WechatArticle(
        title="选课通知", author="中国科学技术大学", url=url, markdown=markdown,
    )
    source_id = "s-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    wechat = FakeWechat([article, _run(_collect_article("第二篇", "蜗壳小道消息", "另一篇内容。", "https://mp.weixin.qq.com/s?src=11&signature=T2"))])
    pipeline = _wechat_pipeline(
        tmp_path, wechat, "科大选课通知在哪里发布？",
        extractor=FixedExtractor([ExtractedClaim(
            text="秋季学期选课通知发布于教学子栏目。",
            evidence=(ExtractedEvidence(source_id=source_id, relation="supports", quote=markdown),),
        )]),
    )
    answer = _run(pipeline.answer("科大选课通知在哪里发布？"))
    assert answer.terminal_reason == "web_evidence_confirmed"
    assert any(s.get("level") == "official_primary" for s in answer.sources)
    assert wechat.calls == 1


def test_wechat_trigger_gate_and_switch(tmp_path) -> None:
    wechat = FakeWechat([])
    pipeline = _wechat_pipeline(
        tmp_path, wechat, "科大选课通知在哪里发布？",
        extractor=FixedExtractor([]),
    )
    _run(pipeline.answer("今天天气怎么样？"))  # 非触发 → 不走微信
    assert wechat.calls == 0
