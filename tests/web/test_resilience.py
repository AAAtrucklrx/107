"""Fail-closed readiness and non-blocking degradation boundaries."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers, parse_sse
from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.api.routers.chat import _event_stream
from xiaowo_web.main import create_app
from xiaowo_web.review import ReviewStore


class UnhealthySidecars:
    async def ready(self) -> bool:
        return False


class CandidateRunner:
    async def run(self, _request: QaRunRequest) -> AnswerBundle:
        return AnswerBundle(
            markdown="当前回答先完成，公开证据随后异步进入审核。",
            terminal_reason="completed",
            ingestion_candidates=[{"source_id": "public-candidate"}],
        )

    def close(self) -> None:
        return None


class FailingRunner:
    async def run(self, _request: QaRunRequest) -> AnswerBundle:
        raise RuntimeError("internal-secret-diagnostic")

    def close(self) -> None:
        return None


class BlockingReviewStore(ReviewStore):
    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.enqueue_started = threading.Event()
        self.enqueue_release = threading.Event()

    def enqueue_candidate(self, namespace: str, candidate: dict, *, now: float | None = None) -> dict:
        self.enqueue_started.set()
        self.enqueue_release.wait(timeout=10)
        return {"namespace": namespace, "candidate": candidate, "status": "queued"}


class TerminalRaceStore:
    def __init__(self) -> None:
        self.reads = 0

    def poll_run(self, _run_id: str, _cursor: int) -> tuple[list[dict], Any]:
        self.reads += 1
        if self.reads == 1:
            # 第一次快照：终态已置但事件尚未读到（run 完成于事件读与状态读之间）
            return [], SimpleNamespace(status="completed")
        # 关流前的最后一次 drain：终态事件在此带出
        return [{
            "id": 4,
            "type": "answer.completed",
            "data": {"stage": "completed"},
        }], SimpleNamespace(status="completed")

    def list_events(self, _run_id: str, _cursor: int) -> list[dict]:
        self.reads += 1
        if self.reads == 1:
            return []
        return [{
            "id": 4,
            "type": "answer.completed",
            "data": {"stage": "completed"},
        }]

    def get_run(self, _run_id: str):
        return SimpleNamespace(status="completed")


class ConnectedRequest:
    def __init__(self, store: TerminalRaceStore) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(store=store))

    async def is_disconnected(self) -> bool:
        return False


def test_terminal_sse_event_is_drained_when_run_finishes_between_reads() -> None:
    store = TerminalRaceStore()

    async def collect() -> list[str]:
        request = ConnectedRequest(store)
        return [chunk async for chunk in _event_stream(request, "race-run", 0)]

    chunks = asyncio.run(collect())

    assert store.reads == 2
    assert len(chunks) == 1
    assert "event: answer.completed" in chunks[0]


def test_unhealthy_sidecars_fail_readiness_without_disabling_liveness(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XIAOWO_SENTINEL_STATE_PATH", str(tmp_path / "no-sentinel.json"))
    settings = make_settings(tmp_path, extra={"XIAOWO_WEB_SEARCH_ENABLED": "true"})
    app = create_app(
        settings,
        runner=ImmediateRunner(),
        sidecar_health_provider=UnhealthySidecars(),
    )
    with TestClient(app) as client:
        live = client.get("/api/v1/health/live")
        ready = client.get("/api/v1/health/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "checks": {
            "database": True,
            "review_database": True,
            "approved_index": False,
            "search_quality": True,
            "web_evidence": False,
            "evidence_extractor": False,
        },
    }


def test_chat_completion_does_not_wait_for_blocked_review_enqueue(tmp_path) -> None:
    settings = make_settings(tmp_path)
    review_store = BlockingReviewStore(settings)
    app = create_app(settings, runner=CandidateRunner(), review_store=review_store)
    with TestClient(app) as client:
        try:
            csrf, _ = bootstrap(client)
            created = client.post(
                "/api/v1/chat/runs",
                json={"question": "公开证据异步入队测试", "mode": "local"},
                headers=mutation_headers(csrf),
            ).json()
            events = parse_sse(client.get("/api/v1" + created["events_url"]).text)
            assert events[-1]["type"] == "answer.completed"
            assert review_store.enqueue_started.wait(timeout=2)
            assert not review_store.enqueue_release.is_set()
        finally:
            review_store.enqueue_release.set()


def test_runner_failure_returns_stable_code_without_exception_text(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=FailingRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        created = client.post(
            "/api/v1/chat/runs",
            json={"question": "公开运行故障测试", "mode": "local"},
            headers=mutation_headers(csrf),
        ).json()
        response = client.get("/api/v1" + created["events_url"])

    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.failed"
    assert events[-1]["data"] == {"code": "INTERNAL_ERROR", "message": "处理请求时发生错误，请重试。"}
    assert "internal-secret-diagnostic" not in response.text
