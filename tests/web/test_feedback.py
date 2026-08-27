"""Feedback ownership, sensitive scanning, encryption, and degraded mode."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers, parse_sse
from xiaowo_web.main import create_app


def _completed_answer(client: TestClient, csrf: str) -> tuple[str, str]:
    created = client.post(
        "/api/v1/chat/runs",
        json={"question": "反馈测试问题", "mode": "local"},
        headers=mutation_headers(csrf),
    ).json()
    events = parse_sse(client.get(created["events_url"]).text)
    completed = next(event for event in events if event["type"] == "answer.completed")
    return created["run_id"], completed["data"]["answer_id"]


def test_category_only_feedback_works_without_data_key(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        run_id, answer_id = _completed_answer(client, csrf)
        response = client.post(
            f"/api/v1/answers/{answer_id}/feedback",
            json={"run_id": run_id, "category": "helpful"},
            headers=mutation_headers(csrf),
        )
        assert response.status_code == 200


def test_feedback_detail_is_scanned_and_encrypted(tmp_path) -> None:
    settings = make_settings(tmp_path, data_key="feedback-data-key")
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        run_id, answer_id = _completed_answer(client, csrf)
        sensitive = client.post(
            f"/api/v1/answers/{answer_id}/feedback",
            json={"run_id": run_id, "category": "incorrect", "detail": "我的学号是 PB25111691"},
            headers=mutation_headers(csrf),
        )
        assert sensitive.status_code == 422
        assert sensitive.json()["error"]["code"] == "FEEDBACK_SENSITIVE"

        accepted = client.post(
            f"/api/v1/answers/{answer_id}/feedback",
            json={"run_id": run_id, "category": "source_issue", "detail": "来源发布日期显示不清楚"},
            headers=mutation_headers(csrf),
        )
        assert accepted.status_code == 200

    with sqlite3.connect(settings.app_db_path) as conn:
        detail = conn.execute("SELECT detail FROM answer_feedback").fetchone()[0]
    assert detail.startswith("gcm:")
    assert "发布日期" not in detail


def test_unencrypted_detail_fails_closed_and_cross_session_answer_is_hidden(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as owner, TestClient(app) as other:
        owner_csrf, _ = bootstrap(owner)
        run_id, answer_id = _completed_answer(owner, owner_csrf)
        no_key = owner.post(
            f"/api/v1/answers/{answer_id}/feedback",
            json={"run_id": run_id, "category": "other", "detail": "普通说明"},
            headers=mutation_headers(owner_csrf),
        )
        assert no_key.status_code == 503
        assert no_key.json()["error"]["code"] == "FEEDBACK_DETAIL_DISABLED"

        other_csrf, _ = bootstrap(other)
        hidden = other.post(
            f"/api/v1/answers/{answer_id}/feedback",
            json={"run_id": run_id, "category": "helpful"},
            headers=mutation_headers(other_csrf),
        )
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "ANSWER_NOT_FOUND"
