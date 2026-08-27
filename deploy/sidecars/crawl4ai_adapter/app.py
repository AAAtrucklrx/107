"""Fail-closed adapter from Xiaowo's crawl contract to Crawl4AI v0.9.2."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


SUPPORTED_VERSION = "0.9.2"
SUPPORTED_SOURCE_REF = "v0.9.2@51e23a0da5ae2ee5f1ada1fc233759340a7790af"
DEFAULT_PROFILE_PATH = Path("/security-profile/crawl4ai-config.yml")
_BLOCKED_HOSTS = frozenset({
    "localhost",
    "metadata",
    "metadata.google.internal",
    "kubernetes.default",
    "kubernetes.default.svc",
})
_ENCODED_IP = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}$", re.I)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class AdapterSettings:
    upstream_url: str = "http://crawl4ai-upstream:11235"
    upstream_token: str = ""
    expected_version: str = SUPPORTED_VERSION
    runtime_attested: bool = False
    profile_path: Path = DEFAULT_PROFILE_PATH
    timeout_seconds: float = 9.0

    @classmethod
    def from_env(cls) -> "AdapterSettings":
        return cls(
            upstream_url=os.environ.get(
                "CRAWL4AI_UPSTREAM_URL", "http://crawl4ai-upstream:11235",
            ).rstrip("/"),
            upstream_token=os.environ.get("CRAWL4AI_UPSTREAM_TOKEN", "").strip(),
            expected_version=os.environ.get(
                "CRAWL4AI_EXPECTED_VERSION", SUPPORTED_VERSION,
            ).strip(),
            runtime_attested=_env_bool("XIAOWO_CRAWL4AI_RUNTIME_ATTESTED"),
            profile_path=Path(os.environ.get(
                "XIAOWO_CRAWL4AI_PROFILE_PATH", str(DEFAULT_PROFILE_PATH),
            )),
            timeout_seconds=float(os.environ.get("CRAWL4AI_UPSTREAM_TIMEOUT", "9")),
        )


class CrawlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: str = Field(min_length=8, max_length=2048)
    respect_robots: Literal[True]
    max_redirects: int = Field(ge=0, le=5)
    max_html_bytes: int = Field(ge=1, le=2 * 1024 * 1024)
    max_pdf_bytes: int = Field(ge=1, le=20 * 1024 * 1024)
    max_pdf_pages: int = Field(ge=1, le=200)
    credentials: None = None


def _validate_public_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only absolute HTTP/HTTPS URLs are allowed")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL credentials and fragments are forbidden")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("URL port is invalid") from exc
    host = parsed.hostname.casefold().rstrip(".")
    if host in _BLOCKED_HOSTS or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("internal hostnames are forbidden")
    if _ENCODED_IP.fullmatch(host):
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("non-canonical IP notation is forbidden") from exc
        if not address.is_global:
            raise ValueError("non-public IP addresses are forbidden")
    else:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            try:
                host.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError("hostname is invalid") from exc
        else:
            if not address.is_global:
                raise ValueError("non-public IP addresses are forbidden")
    return value


def _profile_is_hardened(path: Path) -> bool:
    try:
        profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        crawler = profile["crawler"]
        browser = crawler["browser"]
        base_config = crawler["base_config"]
        security = profile["security"]
        limits = profile["limits"]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return False
    extra_args = {str(item) for item in browser.get("extra_args") or []}
    return bool(
        base_config.get("check_robots_txt") is True
        and crawler.get("rate_limiter", {}).get("enabled") is True
        and security.get("enabled") is True
        and security.get("cors_allow_origins") == []
        and limits.get("wall_clock_s") == 8
        and "--ignore-certificate-errors" not in extra_args
        and "--allow-insecure-localhost" not in extra_args
        and "--disable-web-security" not in extra_args
    )


def _header(headers: Any, name: str) -> str:
    if not isinstance(headers, dict):
        return ""
    wanted = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == wanted:
            return str(value)
    return ""


def _markdown(result: dict[str, Any]) -> str:
    raw = result.get("markdown")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("fit_markdown", "raw_markdown", "markdown_with_citations"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


class Crawl4AiAdapter:
    def __init__(
        self,
        settings: AdapterSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._owned_client = client is None
        headers = {"Accept": "application/json", "User-Agent": "xiaowo-crawl-adapter/1"}
        self._auth_headers: dict[str, str] = {}
        if settings.upstream_token:
            self._auth_headers["Authorization"] = f"Bearer {settings.upstream_token}"
            headers.update(self._auth_headers)
        self.client = client or httpx.AsyncClient(
            base_url=settings.upstream_url,
            headers=headers,
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def health(self) -> dict[str, Any]:
        upstream_ok = False
        upstream_version = ""
        try:
            response = await self.client.get("/health")
            response.raise_for_status()
            payload = response.json()
            upstream_version = str(payload.get("version") or "").strip()
            upstream_ok = payload.get("status") == "ok"
        except (httpx.HTTPError, ValueError, AttributeError):
            upstream_ok = False
        profile_ok = _profile_is_hardened(self.settings.profile_path)
        ready = bool(
            upstream_ok
            and upstream_version == SUPPORTED_VERSION
            and self.settings.expected_version == SUPPORTED_VERSION
            and bool(self.settings.upstream_token)
            and profile_ok
            and self.settings.runtime_attested
        )
        return {
            "status": "ok" if ready else "degraded",
            "upstream_version": upstream_version or None,
            "expected_version": SUPPORTED_VERSION,
            "source_ref": SUPPORTED_SOURCE_REF,
            "egress_protection": ready,
            "robots": ready,
            "allow_internal_urls": False,
            "peer_ip_verification": ready,
            "runtime_attested": self.settings.runtime_attested,
            "profile_valid": profile_ok,
        }

    async def crawl(self, request: CrawlRequest) -> dict[str, Any]:
        try:
            seed_url = _validate_public_url(request.url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        health = await self.health()
        if health["status"] != "ok":
            raise HTTPException(status_code=503, detail="Crawl4AI security profile is not attested")
        upstream_request = {
            "urls": [seed_url],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"headless": True, "text_mode": True},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    "check_robots_txt": True,
                    "cache_mode": "bypass",
                    "page_timeout": 8_000,
                    "stream": False,
                },
            },
        }
        try:
            response = await self.client.post(
                "/crawl",
                json=upstream_request,
                headers=self._auth_headers,
            )
            if response.status_code == 400:
                raise HTTPException(status_code=422, detail="crawl target was blocked")
            response.raise_for_status()
            payload = response.json()
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Crawl4AI upstream failed") from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise HTTPException(status_code=502, detail="Crawl4AI result contract is invalid")
        result = results[0]
        markdown = _markdown(result)
        status_code = int(result.get("status_code") or 0)
        if result.get("success") is not True or not 200 <= status_code < 300 or not markdown.strip():
            raise HTTPException(status_code=422, detail="Crawl4AI returned no usable public text")

        headers = result.get("response_headers") or {}
        content_type = _header(headers, "content-type").split(";", 1)[0].strip().casefold()
        content_type = content_type or "text/html"
        raw_html = result.get("html")
        if isinstance(raw_html, str) and len(raw_html.encode("utf-8")) > request.max_html_bytes:
            raise HTTPException(status_code=413, detail="HTML response exceeds the configured limit")
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        if content_type == "application/pdf":
            raw_length = _header(headers, "content-length")
            page_count = metadata.get("page_count")
            try:
                raw_length_value = int(raw_length)
                page_count_value = int(page_count)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="PDF size or page count could not be verified",
                ) from exc
            if raw_length_value > request.max_pdf_bytes or page_count_value > request.max_pdf_pages:
                raise HTTPException(status_code=413, detail="PDF exceeds the configured limit")
        extracted_limit = request.max_pdf_bytes if content_type == "application/pdf" else request.max_html_bytes
        if len(markdown.encode("utf-8")) > extracted_limit:
            raise HTTPException(status_code=413, detail="extracted text exceeds the configured limit")

        final_url = str(result.get("redirected_url") or result.get("url") or seed_url)
        try:
            _validate_public_url(final_url)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Crawl4AI returned an unsafe final URL") from exc
        title = str(metadata.get("title") or result.get("title") or "").strip()
        published_at = str(
            metadata.get("published_time") or metadata.get("date") or metadata.get("published_at") or "",
        ).strip() or None
        return {
            "final_url": final_url,
            "title": title,
            "markdown": markdown,
            "status_code": status_code,
            "content_type": content_type,
            "fetched_at": datetime.now(UTC).isoformat(),
            "published_at": published_at,
            "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            "robots_allowed": True,
            "peer_ip_verified": True,
        }


def create_app(
    settings: AdapterSettings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    adapter = Crawl4AiAdapter(settings or AdapterSettings.from_env(), client=client)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await adapter.close()

    application = FastAPI(
        title="Xiaowo Crawl4AI Adapter",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, Any]:
        return await adapter.health()

    @application.post("/crawl")
    async def crawl(request: CrawlRequest) -> dict[str, Any]:
        return await adapter.crawl(request)

    return application


app = create_app()
