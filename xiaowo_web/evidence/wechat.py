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
from typing import Any

import httpx

# 允许的目标域名白名单（SSRF 防护：任何跳转/重定向后的最终域都必须在此）
_ALLOWED_FINAL_DOMAINS = frozenset({"mp.weixin.qq.com"})
_ALLOWED_JUMP_DOMAINS = frozenset({"weixin.sogou.com", "mp.weixin.qq.com"})

# 官方号白名单（账号名匹配，2026-09-01 用户确定：中科大/中国科大/蜗壳）
_OFFICIAL_ACCOUNT_RE = re.compile(r"中国科学技术大学|中科大|中国科大|蜗壳")

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_ARTICLE_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]+)"')
_ARTICLE_AUTHOR_RE = re.compile(r'<meta property="og:article:author" content="([^"]+)"')
_ARTICLE_FALLBACK_TITLE_RE = re.compile(r"<h1[^>]*id=\"activity-name\"[^>]*>([^<]+)<")
_JS_URL_FRAG_RE = re.compile(r"url \+= '([^']*)'")
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

    async def close(self) -> None:
        await self._client.aclose()

    # ── 内部实现 ──────────────────────────────────────────────

    async def _search(self, query: str) -> list[WechatSearchHit]:
        await self._bucket.wait("sogou", self._sogou_throttle)
        from urllib.parse import quote

        url = f"https://weixin.sogou.com/weixin?type=2&query={quote(query)}"
        resp = await self._client.get(url)
        if resp.status_code != 200:
            raise WechatUnavailable(f"sogou search HTTP {resp.status_code}")
        self._search_ref = url
        text = resp.text
        if "antispider" in text or "verify" in text:
            raise WechatBlocked("sogou search antispider")
        hits: list[WechatSearchHit] = []
        TITLE_RE = re.compile(r'href="(/link\?url=[^"]+)"[^>]*uigs="article_title_\d+"[^>]*>([\s\S]*?)</a>')
        ACC_RE = re.compile(r'class="all-time-y2">([^<]+)<')
        for block in _BLOCK_SPLIT_RE.split(text)[1:]:
            link_m = TITLE_RE.search(block)
            if not link_m:
                continue
            acc = ACC_RE.search(block)
            account = html.unescape(acc.group(1)).strip() if acc else ""
            title = html.unescape(re.sub(r"<[^>]+>", "", link_m.group(2))).strip()
            if not title:
                continue
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
        if "antispider" in body or "verify" in body:
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
        try:
            resp = await self._client.get(url, headers=headers, follow_redirects=True)
        except httpx.HTTPError as exc:
            self.last_error = f"fetch error: {type(exc).__name__}"
            return None
        page = resp.text
        if "环境异常" in page:
            raise WechatBlocked("article environment-anti-bot")
        if resp.status_code != 200:
            return None
        final = str(resp.url)
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


# ── 提取工具（纯函数，便于单测） ───────────────────────────────

def extract_pure(html_text: str) -> dict[str, Any]:
    m = _ARTICLE_TITLE_RE.search(html_text)
    title = m.group(1) if m else ""
    if not title:
        m2 = _ARTICLE_FALLBACK_TITLE_RE.search(html_text)
        title = html.unescape(m2.group(1)).strip() if m2 else ""
    author_m = _ARTICLE_AUTHOR_RE.search(html_text)
    author = author_m.group(1).strip() if author_m else ""
    content_m = re.search(r'id="js_content"[^>]*>(.*?)</div>', html_text, re.S)
    content = content_m.group(1) if content_m else ""
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
