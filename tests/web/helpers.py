"""Fixtures shared by Web API tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.settings import WebSettings


class ImmediateRunner:
    async def run(self, request: QaRunRequest) -> AnswerBundle:
        return AnswerBundle(
            markdown="这是经过本地测试证据确认的回答。[1]",
            claims=[{
                "claim_id": "c1",
                "text": "这是经过本地测试证据确认的回答。",
                "kind": "factual",
                "status": "confirmed",
                "evidence": [{
                    "source_id": "s1",
                    "evidence_type": "local",
                    "relation": "supports",
                    "excerpt_hash": "fixture-hash",
                    "citation": 1,
                }],
            }],
            sources=[{
                "source_id": "s1",
                "title": "测试知识来源",
                "display_url": None,
                "institution": "小蜗测试夹具",
                "published_at": None,
                "fetched_at": None,
                "level": "local",
                "validity": "active",
                "citation": 1,
            }],
            terminal_reason="completed",
        )

    def close(self) -> None:
        return None


class SlowRunner:
    async def run(self, _request: QaRunRequest) -> AnswerBundle:
        await asyncio.sleep(30)
        return AnswerBundle(markdown="不应完成")

    def close(self) -> None:
        return None


def make_settings(
    tmp_path: Path,
    *,
    mode: str = "anonymous",
    environment: str = "development",
    admin_ids: str = "",
    data_key: str = "",
    public_origin: str = "http://testserver",
    extra: dict[str, str] | None = None,
) -> WebSettings:
    values = {
        "XIAOWO_ENV": environment,
        "XIAOWO_AUTH_MODE": mode,
        "XIAOWO_PUBLIC_ORIGIN": public_origin,
        "XIAOWO_APP_DB_PATH": str(tmp_path / "xiaowo-test.db"),
        "XIAOWO_REVIEW_DB_PATH": str(tmp_path / "review-test.db"),
        "XIAOWO_WEB_EVIDENCE_DIR": str(tmp_path / "web-evidence"),
        "XIAOWO_PUBLISHED_CHROMA_DIR": str(tmp_path / "published-chroma"),
        "XIAOWO_PUBLISHED_BM25_DIR": str(tmp_path / "published-bm25"),
        "XIAOWO_ADMIN_IDS": admin_ids,
        "XIAOWO_DATA_KEY": data_key,
        "XIAOWO_WEB_SEARCH_ENABLED": "false",
    }
    if extra:
        values.update(extra)
    return WebSettings.from_env(values)


def bootstrap(client) -> tuple[str, dict]:
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    payload = response.json()
    return payload["csrf_token"], payload


def mutation_headers(csrf: str, origin: str = "http://testserver") -> dict[str, str]:
    return {"Origin": origin, "X-CSRF-Token": csrf}


def parse_sse(text: str) -> list[dict]:
    import json

    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
