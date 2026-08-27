"""Run inside the adapter image before attesting the Crawl4AI runtime."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx


EXPECTED_VERSION = "0.9.2"
UPSTREAM = os.environ.get("CRAWL4AI_UPSTREAM_URL", "http://crawl4ai-upstream:11235").rstrip("/")
TOKEN = os.environ.get("CRAWL4AI_UPSTREAM_TOKEN", "").strip()


def _request(url: str) -> dict:
    return {
        "urls": [url],
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


async def main() -> int:
    if not TOKEN:
        print("CRAWL4AI_UPSTREAM_TOKEN is required", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(
        base_url=UPSTREAM,
        headers=headers,
        timeout=httpx.Timeout(15.0),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        health = await client.get("/health")
        health.raise_for_status()
        payload = health.json()
        if payload.get("status") != "ok" or payload.get("version") != EXPECTED_VERSION:
            print(f"unexpected Crawl4AI health: {payload}", file=sys.stderr)
            return 3

        blocked = await client.post("/crawl", json=_request("http://169.254.169.254/latest/meta-data/"))
        if blocked.status_code != 400:
            print(f"metadata SSRF probe was not blocked: HTTP {blocked.status_code}", file=sys.stderr)
            return 4

        public = await client.post("/crawl", json=_request("https://example.com/"))
        public.raise_for_status()
        results = public.json().get("results") or []
        if len(results) != 1 or results[0].get("success") is not True:
            print("public crawl probe did not return one successful result", file=sys.stderr)
            return 5

    print("Crawl4AI v0.9.2 runtime probes passed.")
    print("Set XIAOWO_CRAWL4AI_RUNTIME_ATTESTED=true and restart only for this verified deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
