"""Private SearXNG and Crawl4AI sidecar adapters with strict public contracts."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from xiaowo_web.evidence.models import CrawledPage, SearchBatch, SearchHit


class SidecarContractError(RuntimeError):
    pass


class SearxngClient:
    def __init__(self, base_url: str, *, timeout: float = 4.0, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "application/json", "User-Agent": "xiaowo-evidence/1"},
        )

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        response = await self._client.get(
            f"{self.base_url}/search",
            params={"q": query, "format": "json", "language": "zh-CN", "safesearch": 1},
        )
        response.raise_for_status()
        if "json" not in response.headers.get("content-type", "").casefold():
            raise SidecarContractError("SearXNG did not return JSON")
        payload = response.json()
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise SidecarContractError("SearXNG results contract is invalid")
        hits = [
            SearchHit(
                title=str(item.get("title") or "").strip(),
                url=str(item.get("url") or "").strip(),
                snippet=str(item.get("content") or "").strip(),
                engine=str(item.get("engine") or "").strip(),
                published_at=str(item.get("publishedDate") or "").strip() or None,
            )
            for item in raw_results[: max(1, min(limit, 20))]
            if isinstance(item, dict) and item.get("url")
        ]
        unavailable = tuple(
            str(item[0] if isinstance(item, (list, tuple)) and item else item)
            for item in payload.get("unresponsive_engines") or []
        )
        return SearchBatch(hits=hits, partial=bool(unavailable), unavailable_engines=unavailable)

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/", headers={"Accept": "text/html"})
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()


class BochaWebSearchClient:
    """博查 Web Search API 适配器（国内直连，无需 SearXNG sidecar）。

    POST {base}/v1/web-search，Bearer 认证；响应:
    {code:200, data.webPages.value[]: {name, url, snippet, summary, siteName, datePublished}}

    与 SearxngClient 同契约：search(query, limit) → SearchBatch。
    """

    _HEALTH_TTL = 600.0  # 探测结果缓存 10 分钟（探测会消耗一次计费搜索）

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.bochaai.com",
        timeout: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Bocha api_key is required")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        # Authorization 放在请求级（下方 search/health）而非 client 默认头：
        # 外部注入 client 时默认头不会应用，请求级才能保证认证必达
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            trust_env=False,
            headers={"User-Agent": "xiaowo-evidence/1"},
        )
        self._health_ok: bool | None = None
        self._health_at: float = 0.0

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        response = await self._client.post(
            f"{self.base_url}/v1/web-search",
            json={"query": query, "count": max(1, min(limit, 50)), "summary": True, "freshness": "noLimit"},
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 200:
            raise SidecarContractError(f"Bocha search failed: {payload.get('msg') or payload.get('code')}")
        web_pages = ((payload.get("data") or {}).get("webPages") or {}).get("value") or []
        if not isinstance(web_pages, list):
            raise SidecarContractError("Bocha webPages contract is invalid")
        hits = [
            SearchHit(
                title=str(item.get("name") or "").strip(),
                url=str(item.get("url") or "").strip(),
                # summary（正文摘要）优先于 snippet（搜索摘要，可能被截断）
                snippet=str(item.get("summary") or item.get("snippet") or "").strip(),
                engine="bocha",
                published_at=str(item.get("datePublished") or "").strip() or None,
            )
            for item in web_pages
            if isinstance(item, dict) and item.get("url")
        ]
        return SearchBatch(hits=hits[: max(1, min(limit, 50))], partial=False, unavailable_engines=())

    async def health(self) -> bool:
        # 博查无免费探针端点——探测即计费，故缓存 10 分钟
        now = time.monotonic()
        if self._health_ok is not None and now - self._health_at < self._HEALTH_TTL:
            return self._health_ok
        try:
            batch = await self.search("健康检查", limit=1)
            self._health_ok = bool(batch.hits)
        except Exception:
            self._health_ok = False
        self._health_at = now
        return self._health_ok

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()


class Crawl4AiClient:
    def __init__(self, base_url: str, *, timeout: float = 8.0, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "application/json", "User-Agent": "xiaowo-evidence/1"},
        )
        self._security_confirmed = False

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/health")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            self._security_confirmed = False
            return False
        self._security_confirmed = bool(
            payload.get("egress_protection") is True
            and payload.get("robots") is True
            and payload.get("allow_internal_urls") is False
            and payload.get("peer_ip_verification") is True
        )
        return self._security_confirmed

    async def crawl(self, url: str) -> CrawledPage:
        if not self._security_confirmed and not await self.health():
            raise SidecarContractError("Crawl4AI security health contract is not satisfied")
        response = await self._client.post(
            f"{self.base_url}/crawl",
            json={
                "url": url,
                "respect_robots": True,
                "max_redirects": 5,
                "max_html_bytes": 2 * 1024 * 1024,
                "max_pdf_bytes": 20 * 1024 * 1024,
                "max_pdf_pages": 200,
                "credentials": None,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        markdown = str(payload.get("markdown") or "")
        status_code = int(payload.get("status_code") or 0)
        if not 200 <= status_code < 300 or not markdown.strip():
            raise SidecarContractError("Crawl4AI returned an unusable page")
        if len(markdown.encode("utf-8")) > 2 * 1024 * 1024:
            raise SidecarContractError("Crawl4AI extracted text exceeds the limit")
        if payload.get("robots_allowed") is not True or payload.get("peer_ip_verified") is not True:
            raise SidecarContractError("Crawl4AI response lacks robots or peer-IP proof")
        computed_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        provided_hash = str(payload.get("content_hash") or "").strip().casefold()
        if provided_hash and not hmac.compare_digest(provided_hash, computed_hash):
            raise SidecarContractError("Crawl4AI content hash does not match extracted text")
        return CrawledPage(
            requested_url=url,
            final_url=str(payload.get("final_url") or url),
            title=str(payload.get("title") or "").strip(),
            markdown=markdown,
            status_code=status_code,
            content_type=str(payload.get("content_type") or "text/html"),
            fetched_at=str(payload.get("fetched_at") or datetime.now(UTC).isoformat()),
            published_at=str(payload.get("published_at") or "").strip() or None,
            content_hash=computed_hash,
            robots_allowed=True,
            peer_ip_verified=True,
        )

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()


class SidecarHealthProvider:
    def __init__(self, search: SearxngClient, crawler: Crawl4AiClient) -> None:
        self.search = search
        self.crawler = crawler

    async def ready(self) -> bool:
        import asyncio

        search_ok, crawler_ok = await asyncio.gather(self.search.health(), self.crawler.health())
        return bool(search_ok and crawler_ok)

    async def close(self) -> None:
        await self.search.close()
        await self.crawler.close()
