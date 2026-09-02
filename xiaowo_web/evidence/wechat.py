"""WeChat official-account channel for the web evidence pipeline.

Retrieves 微信公众号 content via the sogou weixin search channel (self-managed
session, because sogou link resolution depends on the search-page cookies — a
link fetched without that session hits the antispider wall, verified on
2026-09-01).  Fetches article text plus OCR text of content images through the
platform ``unlimited-ocr`` model (only images yielding >=30 chars or a table
marker are kept).

Compliance: browser UA, throttled requests, robots-safe paths (mp.weixin.qq.com
/s is not disallowed; weixin.sogou.com /weixin is only disallowed for the
``Sogou web spider`` UA), no impersonation of search crawlers, failure -> the
caller falls back to the generic pipeline.  WeChat content is classified as
official only when the account name matches the whitelist; anything else stays
``unverified``.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

import httpx

# 允许的目标域名白名单（SSRF 防护：任何跳转/重定向后的最终域都必须在此）
_ALLOWED_FINAL_DOMAINS = frozenset({"mp.weixin.qq.com"})
_ALLOWED_JUMP_DOMAINS = frozenset({"weixin.sogou.com", "mp.weixin.qq.com"})

# 官方号白名单（账号名匹配，2026-09-01 用户确定：中科大/中国科大/蜗壳）
_OFFICIAL_ACCOUNT_RE = re.compile(r"中国科学技术大学|中科大|中国科大|蜗壳")

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_ARTICLE_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]+)"')
_ARTICLE_AUTHOR_RE = re.compile(r'<meta property="og:article:author" content="([^"]+)"')
_ARTICLE_FALLBACK_TITLE_RE = re.compile(r"<h1[^>]*id=\"activity-name\"[^>]*>([^<]+)<")
_JS_URL_FRAG_RE = re.compile(r"url \+= '([^']*)'")
# 反爬判定特征：仅匹配验证码/拦截页特征串，不再用宽泛的 "verify"
#（正常页面 JS 里的 verified/unverified 会误触发，连续 3 次即熔断 600s）
_ANTISPIDER_RE = re.compile(r"antispider|seccode|snssimid|/website/antispider", re.I)
_IMAGE_RE = re.compile(r"https?://mmbiz\.qpic\.cn/[A-Za-z0-9_./=-]+")
_HREF_RE = re.compile(r'href="(/link\?url=[^"]+)"')
_ACCOUNT_RE = re.compile(r'class="account"[^>]*>([^<]+)<')
_BLOCK_SPLIT_RE = re.compile(r'class="(?:txt-box|news-box|news-bigbox)"')

_OCR_MIN_CHARS = 30
_OCR_MAX_IMAGES_PER_ARTICLE = 2
_MAX_ARTICLES = 5
_OCR_THROTTLE_SECONDS = 2.5
_SOGOU_THROTTLE_SECONDS = 3.0
_ARTICLE_THROTTLE_SECONDS = 3.0
_BLOCK_THRESHOLD = 3
_BLOCK_COOLDOWN_SECONDS = 600
_FETCH_TIMEOUT = 20.0
_SEARCH_TIMEOUT = 15.0


class WechatBlocked(RuntimeError):
    """搜狗/微信反爬拦截（验证码墙或环境异常）。"""


class WechatUnavailable(RuntimeError):
    """协议层失败（网络/格式/未解析出 URL）。"""


@dataclass(slots=True)
class WechatSearchHit:
    title: str
    account: str
    link: str


@dataclass(slots=True)
class WechatArticle:
    title: str
    author: str
    url: str
    markdown: str
    published_at: str | None = None
    ocr_spans: list[str] = field(default_factory=list)
    raw_html: str = ""


@dataclass(slots=True)
class WechatBundle:
    articles: list[WechatArticle]
    partial: bool = False
    blocked: bool = False


class _TokenBucket:
    """Process-level minimum-interval throttle per key."""

    def __init__(self) -> None:
        self._next_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, key: str, interval: float) -> None:
        async with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_at.get(key, 0.0))
            self._next_at[key] = slot + interval
        delay = slot - now
        if delay > 0:
            await asyncio.sleep(delay)


class WechatClient:
    """Self-session wechat retrieval + article extract + image OCR."""

    def __init__(
        self,
        *,
        ocr_model: str = "unlimited-ocr",
        block_threshold: int = _BLOCK_THRESHOLD,
        block_cooldown: float = _BLOCK_COOLDOWN_SECONDS,
        sogou_throttle: float = _SOGOU_THROTTLE_SECONDS,
        article_throttle: float = _ARTICLE_THROTTLE_SECONDS,
        ocr_throttle: float = _OCR_THROTTLE_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(_FETCH_TIMEOUT, connect=10.0),
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            follow_redirects=False,
        )
        self.ocr_model = ocr_model
        self._bucket = _TokenBucket()
        self._failures = 0
        self._open_until = 0.0
        self._block_threshold = block_threshold
        self._block_cooldown = block_cooldown
        self._sogou_throttle = sogou_throttle
        self._article_throttle = article_throttle
        self._ocr_throttle = ocr_throttle
        self._search_ref = ""  # 最近一次搜索页 URL，作为 resolve 的 Referer
        self.last_error: str | None = None

    @property
    def circuit_open(self) -> bool:
        return time.monotonic() < self._open_until

    def _note(self, ok: bool) -> None:
        if ok:
            self._failures = 0
            return
        self._failures += 1
        if self._failures >= self._block_threshold:
            self._open_until = time.monotonic() + self._block_cooldown
            self.last_error = f"wechat circuit open ({self._failures} consecutive failures)"

    async def collect(self, query: str) -> WechatBundle:
        """Search -> resolve -> fetch -> extract (+OCR) with bounded budget."""
        if self.circuit_open:
            return WechatBundle([], blocked=True)
        try:
            hits = await self._search(query)
            # 官方号优先（白名单提权：官方一手 > 普通公众号）
            hits.sort(key=lambda h: (0 if is_official_account(h.account) else 1,))
            if not hits:
                return WechatBundle([], partial=True)
            articles: list[WechatArticle] = []
            seen_links: set[str] = set()
            ocr_budget = _OCR_MAX_IMAGES_PER_ARTICLE  # 单次收集 ≤3 张（保时间预算）
            for hit in hits[:_MAX_ARTICLES]:
                if hit.link in seen_links:
                    continue
                seen_links.add(hit.link)
                try:
                    article = await self._resolve_and_fetch(hit)
                except WechatBlocked:
                    self._note(False)
                    return WechatBundle([], blocked=True)
                if article is None:
                    continue
                if ocr_budget > 0 and len(article.markdown) < 400:
                    # 正文已足够（>400 字）时不再 OCR：长文数字往往已在正文；只在“图主导”短文上跑 OCR
                    spans, used = await self._ocr_images(article.raw_html, min(ocr_budget, _OCR_MAX_IMAGES_PER_ARTICLE))
                    ocr_budget -= used
                    article.ocr_spans = spans
                articles.append(article)
                if len(articles) >= 2:
                    break
            self._note(True)
            return WechatBundle(articles=articles, partial=len(articles) < 2)
        except WechatBlocked:
            self._note(False)
            return WechatBundle([], blocked=True)
        except (WechatUnavailable, httpx.HTTPError, OSError) as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            self._note(False)
            return WechatBundle([], blocked=self.circuit_open)

    async def collect_account(
        self,
        account_name: str,
        *,
        queries: list[str] | None = None,
        pages: int = 2,
        limit: int = 30,
    ) -> list[WechatArticle]:
        """账号定向采集：以目标公众号名为查询词（可多词）分页检索 → 按 sourcename 过滤 → 全量解析+抓取。"""
        if self.circuit_open:
            return []
        found: dict[str, WechatSearchHit] = {}
        try:
            for q in (queries or [account_name]):
                for page in range(1, max(1, pages) + 1):
                    for hit in await self._search(q, page=page):
                        if hit.link not in found:
                            found[hit.link] = hit
                    await asyncio.sleep(0.5)
        except (WechatBlocked, WechatUnavailable, httpx.HTTPError, OSError):
            return []
        # WAP 结果常缺 sourcename → 统一抓取后按文章真实作者（og:article:author）判定归属
        articles: list[WechatArticle] = []
        for link, hit in list(found.items())[: limit * 3]:
            try:
                article = await self._resolve_and_fetch(hit)
            except WechatBlocked:
                self._note(False)
                break
            if article is not None and article.author == account_name:
                articles.append(article)
            if len(articles) >= limit:
                break
        self._note(True)
        return articles

    async def collect_many(
        self,
        query: str,
        *,
        limit: int = 3,
        ocr_budget: int = 6,
    ) -> list[WechatArticle]:
        """批量采集：搜索→解析→抓取→（图主导时）OCR，用于知识入库（无熔断限制，供采集脚本用）。"""
        if self.circuit_open:
            return []
        try:
            hits = await self._search(query)
        except (WechatBlocked, WechatUnavailable, httpx.HTTPError, OSError):
            return []
        hits.sort(key=lambda h: (0 if is_official_account(h.account) else 1,))
        articles: list[WechatArticle] = []
        seen: set[str] = set()
        for hit in hits[: max(limit * 3, limit)]:
            if len(articles) >= limit:
                break
            if hit.link in seen:
                continue
            seen.add(hit.link)
            try:
                article = await self._resolve_and_fetch(hit)
            except WechatBlocked:
                self._note(False)
                break
            if article is None:
                continue
            if ocr_budget > 0 and len(article.markdown) < 400:
                spans, used = await self._ocr_images(
                    article.raw_html, min(ocr_budget, _OCR_MAX_IMAGES_PER_ARTICLE),
                )
                ocr_budget -= used
                article.ocr_spans = spans
            articles.append(article)
        self._note(True)
        return articles

    async def close(self) -> None:
        await self._client.aclose()

    # ── 内部实现 ──────────────────────────────────────────────

    async def _search(self, query: str, *, page: int = 1) -> list[WechatSearchHit]:
        await self._bucket.wait("sogou", self._sogou_throttle)
        from urllib.parse import quote

        url = f"https://weixin.sogou.com/weixin?type=2&query={quote(query)}&page={max(1, int(page))}"
        resp = await self._client.get(url)
        text = resp.text if resp.status_code == 200 else ""
        if _ANTISPIDER_RE.search(text) or resp.status_code != 200:
            # 桌面端点被反爬（2026-09-02 实测 302→antispider）：回退 WAP 端点（独立反爬策略）
            try:
                resp = await self._client.get(
                    url.replace("/weixin?", "/weixinwap?"),
                    headers={"User-Agent": _MOBILE_UA},
                )
                text = resp.text if resp.status_code == 200 else ""
                self._search_ref = url.replace("/weixin?", "/weixinwap?")
            except httpx.HTTPError:
                text = ""
        else:
            self._search_ref = url
        if not text or _ANTISPIDER_RE.search(text):
            raise WechatBlocked("sogou search antispider")
        hits: list[WechatSearchHit] = []
        # 桌面(article_title_x)与 WAP(data-uigs="article_title_x")统一匹配；账号：all-time-y2 或 data-sourcename
        TITLE_RE = re.compile(r'href="(/link\?url=[^"]+)"[^>]*uigs="article_title_\d+"[^>]*>([\s\S]*?)</a>')
        ACC_RE = re.compile(r'class="all-time-y2">([^<]+)<|data-sourcename="([^"]+)"')
        for link_m in TITLE_RE.finditer(text):
            title = html.unescape(re.sub(r"<[^>]+>", "", link_m.group(2))).strip()
            if not title:
                continue
            tail = text[link_m.end(): link_m.end() + 600]
            acc = ACC_RE.search(tail)
            account = html.unescape((acc.group(1) or acc.group(2) or "")).strip() if acc else ""
            hits.append(WechatSearchHit(
                title=title[:80],
                account=account[:40],
                link="https://weixin.sogou.com" + link_m.group(1).replace("&amp;", "&"),
            ))
        return hits

    async def _resolve_and_fetch(self, hit: WechatSearchHit) -> WechatArticle | None:
        await self._bucket.wait("sogou", self._sogou_throttle)
        resp = await self._client.get(
            hit.link, headers={"Referer": self._search_ref or "https://weixin.sogou.com/"},
        )
        body = resp.text
        if _ANTISPIDER_RE.search(body):
            raise WechatBlocked("resolve antispider")
        real_url = "".join(_JS_URL_FRAG_RE.findall(body)).strip()
        if not real_url:
            return None
        return await self._fetch_article(real_url, hit.title, hit.account)

    async def _fetch_article(self, url: str, fallback_title: str, account: str) -> WechatArticle | None:
        if not self._domain_ok(url, _ALLOWED_JUMP_DOMAINS):
            self.last_error = "reject domain: " + url[:60]
            raise WechatUnavailable("article URL domain not allowed")
        await self._bucket.wait("article", self._article_throttle)
        headers = {"Referer": "https://weixin.qq.com/"}
        # 手动逐跳跟随：每跳先过域白名单再请求，杜绝中间跳指向内网的盲 SSRF
        #（follow_redirects=True 会先实际请求中间跳，之后才校验最终域名）
        current = url
        try:
            resp = await self._client.get(current, headers=headers, follow_redirects=False)
            for _ in range(5):
                if not resp.is_redirect:
                    break
                nxt = str(resp.next_request.url) if resp.next_request is not None else ""
                if not nxt or not (
                    self._domain_ok(nxt, _ALLOWED_JUMP_DOMAINS)
                    or self._domain_ok(nxt, _ALLOWED_FINAL_DOMAINS)
                ):
                    self.last_error = "redirect target not allowed: " + nxt[:60]
                    return None
                current = nxt
                resp = await self._client.get(current, headers=headers, follow_redirects=False)
        except httpx.HTTPError as exc:
            self.last_error = f"fetch error: {type(exc).__name__}"
            return None
        page = resp.text
        if "环境异常" in page:
            raise WechatBlocked("article environment-anti-bot")
        if resp.status_code != 200:
            return None
        final = current
        if not self._domain_ok(final, _ALLOWED_FINAL_DOMAINS):
            self.last_error = "redirect target not allowed: " + final[:60]
            return None
        pure = extract_pure(page)
        return WechatArticle(
            title=fallback_title or pure["title"] or "",
            author=account or pure["author"] or "",
            url=final,
            markdown=pure["text"] or (fallback_title or "(无正文)"),
            published_at=_extract_published_at(page),
            raw_html=page,
        )

    async def _ocr_images(self, raw_html: str, budget: int) -> tuple[list[str], int]:
        images = list(dict.fromkeys(_IMAGE_RE.findall(raw_html or "")))[:budget]
        if not images:
            return [], 0
        # 并发抽取（节流器仍以固定间隔起跑，避免对 OCR 平台突发压力）
        locks = [asyncio.Lock() for _ in images[:3]]

        async def _one(idx: int, img: str) -> str | None:
            async with locks[min(idx, len(locks) - 1)]:
                await self._bucket.wait("ocr", self._ocr_throttle)
                return await self._ocr_once(img)

        results = await asyncio.gather(*[_one(i, img) for i, img in enumerate(images)])
        kept: list[str] = []
        used = 0
        for text in results:
            used += 1
            if text and (len(text) >= _OCR_MIN_CHARS or "table" in text[:40].lower()):
                kept.append(f"[图{used}·OCR] {text[:500]}")
        return kept, used

    async def _ocr_once(self, image_url: str) -> str | None:
        base = _llm_base_url()
        if not base:
            return None
        payload = {
            "model": self.ocr_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "识别这张图片里的全部文字，如果是图表或表格，请把关键数字和数据一起列出"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
        }
        try:
            resp = await self._client.post(
                base.rstrip("/") + "/chat/completions", json=payload,
                headers=_llm_auth_header(),
                timeout=httpx.Timeout(15.0),
            )
            if resp.status_code != 200:
                self.last_error = f"ocr HTTP {resp.status_code}"
                return None
            data = resp.json()
            return data["choices"][0]["message"].get("content") or None
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _domain_ok(url: str, allowed: frozenset[str]) -> bool:
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname or ""
        return host in allowed


# ── 提取工具（纯函数，便于单测） ─────────────────────────────

# HTML 空元素（无闭合标签）：不参与嵌套深度计数
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


class _JsContentTextParser(HTMLParser):
    """提取 id="js_content" 节点内的全部文本。

    公众号正文普遍嵌套 div/section，旧的非贪婪正则 `(.*?)</div>` 会在
    第一个内层闭合标签处截断，导致正文大量丢失、quote 校验命中率骤降。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._depth = 0
        self._skip = 0  # js_content 内部 script/style 内容不计入正文
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if not self._capture:
            for key, value in attrs:
                if key == "id" and value == "js_content":
                    self._capture = True
                    self._depth = 0
                    self._skip = 0
                    break
            return
        if tag in _VOID_TAGS:
            return
        self._depth += 1
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if not self._capture:
            return
        if self._skip and tag in ("script", "style"):
            self._skip -= 1
            return
        if self._depth == 0:
            self._capture = False
        else:
            self._depth -= 1

    def handle_startendtag(self, tag, attrs):
        # 自闭合标签（<br/> 等）不改变嵌套深度
        pass

    def handle_data(self, data):
        if self._capture and not self._skip and data:
            self.parts.append(data)


def _extract_js_content(html_text: str) -> str:
    """提取 js_content 正文；解析器异常时回退到结束锚点正则。"""
    parser = _JsContentTextParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # noqa: BLE001  # html.parser 对残缺标签宽容，仍防御性兜底
        parser.parts = []
        anchor_m = re.search(
            r'id="js_content"[^>]*>([\s\S]*?)</div>\s*(?:<script|<div class="rich_media_tool)',
            html_text,
        )
        if anchor_m:
            return anchor_m.group(1)
    return " ".join(parser.parts)


def extract_pure(html_text: str) -> dict[str, Any]:
    m = _ARTICLE_TITLE_RE.search(html_text)
    title = m.group(1) if m else ""
    if not title:
        m2 = _ARTICLE_FALLBACK_TITLE_RE.search(html_text)
        title = html.unescape(m2.group(1)).strip() if m2 else ""
    author_m = _ARTICLE_AUTHOR_RE.search(html_text)
    author = author_m.group(1).strip() if author_m else ""
    content = _extract_js_content(html_text)
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", text).strip()
    images = list(dict.fromkeys(_IMAGE_RE.findall(html_text)))
    return {"title": title, "author": author, "text": text, "images": images}


def is_official_account(account_name: str) -> bool:
    return bool(_OFFICIAL_ACCOUNT_RE.search(account_name or ""))


def build_markdown(article_text: str, ocr_spans: list[str]) -> str:
    parts = [article_text]
    if ocr_spans:
        parts.append("\n\n".join(ocr_spans))
    return "\n\n".join(p for p in parts if p)


def _extract_published_at(html_text: str) -> str | None:
    m = re.search(r'(?:var createTime = |"createTime":\s*["\']?)(\d{10})', html_text)
    if m:
        import datetime as _dt

        try:
            return _dt.datetime.fromtimestamp(int(m.group(1)), tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    m2 = re.search(r'time ="(\d{4}-\d{2}-\d{2})', html_text)
    return m2.group(1) if m2 else None


def _llm_base_url() -> str:
    import os

    return os.getenv("LLM_BASE_URL", "").strip()


def _llm_auth_header() -> dict[str, str]:
    import os

    key = os.getenv("LLM_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def article_content_hash(article: WechatArticle) -> str:
    payload = f"{article.url}|{article.title}|{article.markdown}|{'|'.join(article.ocr_spans)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
