"""The deployment adapter must expose only Xiaowo's fail-closed contract."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from deploy.sidecars.crawl4ai_adapter.app import (
    AdapterSettings,
    _profile_is_hardened,
    create_app,
)


def _profile(tmp_path):
    path = tmp_path / "crawl4ai-config.yml"
    path.write_text(
        """
limits:
  wall_clock_s: 8
llm:
  enabled: false
  provider: disabled
  allowed_providers: []
  api_key: null
  base_url: null
redis:
  password: null
  password_env: REDIS_PASSWORD
security:
  enabled: true
  cors_allow_origins: []
crawler:
  base_config:
    check_robots_txt: true
  rate_limiter:
    enabled: true
  browser:
    extra_args: []
""".strip(),
        encoding="utf-8",
    )
    return path


def _settings(tmp_path, *, attested: bool) -> AdapterSettings:
    return AdapterSettings(
        upstream_url="http://crawl4ai-upstream:11235",
        upstream_token="fixture-token",
        runtime_attested=attested,
        profile_path=_profile(tmp_path),
        redis_password_configured=True,
    )


def test_tracked_crawl4ai_profile_has_no_llm_or_redis_secret_fallback() -> None:
    profile = Path(__file__).parents[2] / "deploy" / "sidecars" / "crawl4ai" / "config.yml"
    text = profile.read_text(encoding="utf-8")
    assert "gpt-4o-mini" not in text
    assert _profile_is_hardened(profile, redis_password_configured=True) is True


def test_health_fails_closed_until_runtime_is_attested(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "version": "0.9.2"})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://crawl4ai-upstream")
    app = create_app(_settings(tmp_path, attested=False), client=upstream)
    with TestClient(app) as client:
        payload = client.get("/health").json()
    assert payload["status"] == "degraded"
    assert payload["egress_protection"] is False
    assert payload["runtime_attested"] is False
    assert payload["profile_valid"] is True
    assert payload["llm_disabled"] is True
    assert payload["redis_password_configured"] is True


def test_crawl_translates_upstream_contract_without_credentials(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.9.2"})
        assert request.url.path == "/crawl"
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert "cookie" not in request.headers
        body = json.loads(request.content)
        assert body["urls"] == ["https://example.com/public"]
        assert body["crawler_config"]["params"]["check_robots_txt"] is True
        assert "credentials" not in json.dumps(body).casefold()
        return httpx.Response(200, json={
            "success": True,
            "results": [{
                "url": "https://example.com/public",
                "redirected_url": "https://example.com/final",
                "success": True,
                "status_code": 200,
                "html": "<main>public text</main>",
                "markdown": {"raw_markdown": "Public source text."},
                "metadata": {"title": "Public page", "date": "2026-08-27"},
                "response_headers": {"Content-Type": "text/html; charset=utf-8"},
            }],
        })

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://crawl4ai-upstream")
    app = create_app(_settings(tmp_path, attested=True), client=upstream)
    with TestClient(app) as client:
        response = client.post("/crawl", json={
            "url": "https://example.com/public",
            "respect_robots": True,
            "max_redirects": 5,
            "max_html_bytes": 2 * 1024 * 1024,
            "max_pdf_bytes": 20 * 1024 * 1024,
            "max_pdf_pages": 200,
            "credentials": None,
        })
    assert response.status_code == 200
    payload = response.json()
    assert payload["final_url"] == "https://example.com/final"
    assert payload["markdown"] == "Public source text."
    assert payload["robots_allowed"] is True
    assert payload["peer_ip_verified"] is True
    assert len(payload["content_hash"]) == 64


def test_adapter_rejects_private_targets_and_credentials_before_upstream(tmp_path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "ok", "version": "0.9.2"})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://crawl4ai-upstream")
    app = create_app(_settings(tmp_path, attested=True), client=upstream)
    base = {
        "respect_robots": True,
        "max_redirects": 5,
        "max_html_bytes": 2 * 1024 * 1024,
        "max_pdf_bytes": 20 * 1024 * 1024,
        "max_pdf_pages": 200,
        "credentials": None,
    }
    with TestClient(app) as client:
        private = client.post("/crawl", json={**base, "url": "http://169.254.169.254/latest/meta-data/"})
        credentialed = client.post("/crawl", json={
            **base,
            "url": "https://example.com/",
            "credentials": "secret",
        })
    assert private.status_code == 422
    assert credentialed.status_code == 422
    assert calls == 0
