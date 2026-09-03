"""Sidecar adapters must not forward credentials and must verify security health."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from xiaowo_web.evidence.clients import BochaWebSearchClient, Crawl4AiClient, SearxngClient, SidecarContractError


def test_bocha_parses_web_pages_and_sends_bearer_key() -> None:
    async def scenario() -> None:
        seen_auth = None

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_auth
            seen_auth = request.headers.get("authorization")
            assert request.url.path == "/v1/web-search"
            body = json.loads(request.content)
            assert body["query"]
            assert body["summary"] is True
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "code": 200,
                    "data": {
                        "webPages": {
                            "value": [
                                {"name": "科大通知", "url": "https://ustc.edu.cn/a", "summary": "正文摘要", "datePublished": "2026-09-01T10:00:00+08:00"},
                                {"url": ""},
                            ]
                        }
                    },
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bochaai.com")
        adapter = BochaWebSearchClient("sk-test", client=client)
        batch = await adapter.search("查询", limit=5)
        assert seen_auth == "Bearer sk-test"
        assert batch.partial is False
        assert len(batch.hits) == 1
        assert batch.hits[0].title == "科大通知"
        assert batch.hits[0].snippet == "正文摘要"
        assert batch.hits[0].engine == "bocha"
        assert batch.hits[0].published_at == "2026-09-01T10:00:00+08:00"
        await client.aclose()

    asyncio.run(scenario())


def test_bocha_health_fails_closed_on_error() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "application/json"}, json={"code": 401, "msg": "key invalid"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bochaai.com")
        adapter = BochaWebSearchClient("sk-test", client=client)
        assert await adapter.health() is False
        await client.aclose()

    asyncio.run(scenario())


def test_searxng_parses_partial_results() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/search"
            assert request.url.params["format"] == "json"
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "results": [{"title": "A", "url": "https://example.com/a", "content": "text", "engine": "bing"}],
                    "unresponsive_engines": [["brave", "timeout"]],
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://searxng")
        adapter = SearxngClient("http://searxng", client=client)
        batch = await adapter.search("公开查询")
        assert batch.partial is True
        assert batch.unavailable_engines == ("brave",)
        assert batch.hits[0].title == "A"
        await client.aclose()

    asyncio.run(scenario())


def test_crawl4ai_requires_security_health_and_sends_no_credentials() -> None:
    async def scenario() -> None:
        seen_crawl = False

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_crawl
            if request.url.path == "/health":
                return httpx.Response(200, json={
                    "egress_protection": True,
                    "robots": True,
                    "allow_internal_urls": False,
                    "peer_ip_verification": True,
                })
            seen_crawl = True
            assert "cookie" not in request.headers
            assert "authorization" not in request.headers
            payload = json.loads(request.content)
            assert payload["credentials"] is None
            assert payload["respect_robots"] is True
            return httpx.Response(200, json={
                "final_url": payload["url"],
                "title": "Page",
                "markdown": "公开页面正文，包含足够内容。",
                "status_code": 200,
                "content_type": "text/html",
                "robots_allowed": True,
                "peer_ip_verified": True,
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://crawl4ai")
        adapter = Crawl4AiClient("http://crawl4ai", client=client)
        page = await adapter.crawl("https://example.com/public")
        assert seen_crawl is True
        assert page.status_code == 200
        await client.aclose()

    asyncio.run(scenario())


def test_crawl4ai_rejects_incomplete_health_contract() -> None:
    async def scenario() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "egress_protection": True,
                "robots": True,
                "allow_internal_urls": False,
                "peer_ip_verification": False,
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://crawl4ai")
        adapter = Crawl4AiClient("http://crawl4ai", client=client)
        with pytest.raises(SidecarContractError):
            await adapter.crawl("https://example.com/public")
        await client.aclose()

    asyncio.run(scenario())


def test_crawl4ai_rejects_a_mismatched_content_hash() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={
                    "egress_protection": True,
                    "robots": True,
                    "allow_internal_urls": False,
                    "peer_ip_verification": True,
                })
            payload = json.loads(request.content)
            return httpx.Response(200, json={
                "final_url": payload["url"],
                "title": "Page",
                "markdown": "公开页面正文。",
                "content_hash": "0" * 64,
                "status_code": 200,
                "content_type": "text/html",
                "robots_allowed": True,
                "peer_ip_verified": True,
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://crawl4ai")
        adapter = Crawl4AiClient("http://crawl4ai", client=client)
        with pytest.raises(SidecarContractError):
            await adapter.crawl("https://example.com/public")
        await client.aclose()

    asyncio.run(scenario())
