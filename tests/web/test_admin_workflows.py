"""Stable pagination and reviewer publication-state API workflows."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.main import create_app
from xiaowo_web.review import ReviewStore
from xiaowo_web.review.publisher import IndexArtifact, PublicationWorker
from xiaowo_web.worker import IngestionWorker


class _RecordingWriter:
    def __init__(self, kind: str, *, fail: bool = False) -> None:
        self.kind = kind
        self.fail = fail

    def write(self, _namespace: str, generation_id: str, documents: list[dict]) -> IndexArtifact:
        if self.fail:
            raise RuntimeError(f"{self.kind} failed")
        return IndexArtifact(
            self.kind,
            f"fixture://{self.kind}/{generation_id}",
            len(documents),
            f"{self.kind}-hash",
        )


def _candidate(suffix: str) -> dict:
    return {
        "source_id": f"source-{suffix}",
        "normalized_url": f"https://example.com/{suffix}",
        "final_url": f"https://example.com/{suffix}",
        "title": f"公开资料 {suffix}",
        "institution": "测试机构",
        "level": "reliable_independent",
        "fetched_at": "2026-08-27T00:00:00Z",
        "content_type": "text/html",
        "snapshot_text": f"公开资料 {suffix} 的正文足够长，用于稳定分页和发布状态测试。",
        "evidence_span_hash": f"span-{suffix}",
    }


def _approve_one(store: ReviewStore, suffix: str) -> str:
    store.enqueue_candidate("demo", _candidate(suffix))
    assert IngestionWorker(store, worker_id=f"cleaner-{suffix}").run_once() == "done"
    item = next(value for value in store.list_items("demo") if value["title"].endswith(suffix))
    item_id = str(item["item_id"])
    store.start_review("demo", item_id, "reviewer", f"start-{suffix}")
    detail = store.get_item("demo", item_id)
    assert detail is not None
    current_version = detail["current_version"]
    current_version_ids = {
        version["version_id"]
        for version in detail["versions"]
        if version["version_number"] == current_version
    }
    chunk = next(value for value in detail["chunks"] if value["version_id"] in current_version_ids)
    store.set_chunk_approval(
        "demo", item_id, chunk["chunk_id"], True, "reviewer", f"chunk-{suffix}",
    )
    store.approve_item(
        "demo",
        item_id,
        category="announcement",
        ttl_days=7,
        actor_key="reviewer",
        request_id=f"approve-{suffix}",
    )
    return item_id


def _login_demo_admin(client: TestClient) -> str:
    csrf, _ = bootstrap(client)
    response = client.post("/api/v1/auth/demo", headers=mutation_headers(csrf))
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def test_conversation_and_review_lists_use_stable_opaque_cursors(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    review_store = ReviewStore(settings)
    review_store.initialize()
    for suffix in ("one", "two", "three"):
        review_store.enqueue_candidate("demo", _candidate(suffix))
        assert IngestionWorker(review_store, worker_id=f"worker-{suffix}").run_once() == "done"

    app = create_app(settings, runner=ImmediateRunner(), review_store=review_store)
    with TestClient(app) as client:
        csrf = _login_demo_admin(client)
        for title in ("会话一", "会话二", "会话三"):
            created = client.post(
                "/api/v1/conversations",
                json={"title": title},
                headers=mutation_headers(csrf),
            )
            assert created.status_code == 200

        conversation_ids: list[str] = []
        cursor: str | None = None
        while True:
            params = {"limit": 1}
            if cursor:
                params["cursor"] = cursor
            page = client.get("/api/v1/conversations", params=params)
            assert page.status_code == 200
            payload = page.json()
            conversation_ids.extend(item["conversation_id"] for item in payload["items"])
            cursor = payload["next_cursor"]
            if not cursor:
                break
        assert len(conversation_ids) == len(set(conversation_ids)) == 3

        review_ids: list[str] = []
        cursor = None
        while True:
            params = {"limit": 1}
            if cursor:
                params["cursor"] = cursor
            page = client.get("/api/v1/admin/review-items", params=params)
            assert page.status_code == 200
            payload = page.json()
            review_ids.extend(item["item_id"] for item in payload["items"])
            cursor = payload["next_cursor"]
            if not cursor:
                break
        assert len(review_ids) == len(set(review_ids)) == 3
        invalid = client.get("/api/v1/admin/review-items", params={"cursor": "not-a-cursor"})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "CURSOR_INVALID"


def test_demo_feedback_list_is_paginated_and_namespace_isolated(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        mode="demo",
        admin_ids="PB25111691",
        data_key="review-feedback-key",
    )
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as client:
        _login_demo_admin(client)
        app.state.store.create_feedback(
            answer_id="demo-answer-1",
            run_id="demo-run-1",
            category="helpful",
            detail="",
            namespace="demo",
            now=100,
        )
        app.state.store.create_feedback(
            answer_id="demo-answer-2",
            run_id="demo-run-2",
            category="source_issue",
            detail="来源日期需要核对",
            namespace="demo",
            now=101,
        )
        app.state.store.create_feedback(
            answer_id="production-answer",
            run_id="production-run",
            category="incorrect",
            detail="",
            namespace="production",
            now=102,
        )

        first = client.get("/api/v1/admin/feedback", params={"limit": 1})
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["namespace"] == "demo"
        assert [item["answer_id"] for item in first_payload["items"]] == ["demo-answer-2"]
        second = client.get(
            "/api/v1/admin/feedback",
            params={"limit": 1, "cursor": first_payload["next_cursor"]},
        )
        assert second.status_code == 200
        assert [item["answer_id"] for item in second.json()["items"]] == ["demo-answer-1"]
        assert second.json()["next_cursor"] is None


def test_revoke_active_item_queues_a_new_generation_through_api(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _approve_one(store, "revoke")
    worker = PublicationWorker(
        store,
        settings,
        vector_writer=_RecordingWriter("chroma"),
        bm25_writer=_RecordingWriter("bm25"),
        worker_id="publisher-active",
    )
    assert worker.run_once() == "active"

    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf = _login_demo_admin(client)
        response = client.post(
            f"/api/v1/admin/review-items/{item_id}/revoke",
            headers=mutation_headers(csrf),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "revoked"
        assert store.get_item("demo", item_id)["status"] == "revoked"

    with sqlite3.connect(settings.review_db_path) as conn:
        queued = conn.execute(
            "SELECT reason FROM publish_jobs WHERE namespace = 'demo' AND status = 'queued'"
        ).fetchall()
    assert [row[0] for row in queued] == [f"item_revoked:{item_id}"]


def test_failed_publish_can_be_retried_once_through_api(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _approve_one(store, "retry")
    worker = PublicationWorker(
        store,
        settings,
        vector_writer=_RecordingWriter("chroma", fail=True),
        bm25_writer=_RecordingWriter("bm25"),
        worker_id="publisher-failed",
    )
    assert worker.run_once() == "retry"
    assert store.get_item("demo", item_id)["status"] == "publish_failed"

    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf = _login_demo_admin(client)
        retried = client.post(
            f"/api/v1/admin/review-items/{item_id}/publish/retry",
            headers=mutation_headers(csrf),
        )
        assert retried.status_code == 200
        assert retried.json()["status"] == "pending_publish"
        repeated = client.post(
            f"/api/v1/admin/review-items/{item_id}/publish/retry",
            headers=mutation_headers(csrf),
        )
        assert repeated.status_code == 409
        assert repeated.json()["error"]["code"] == "REVIEW_STATE_INVALID"
