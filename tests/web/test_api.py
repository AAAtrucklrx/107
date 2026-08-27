"""API contracts for auth, CSRF, run ownership, SSE, and history."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.web.helpers import (
    ImmediateRunner,
    SlowRunner,
    bootstrap,
    make_settings,
    mutation_headers,
    parse_sse,
)
from xiaowo_web.main import create_app


def test_public_config_does_not_expose_internal_configuration(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=ImmediateRunner())
    with TestClient(app) as client:
        payload = client.get("/api/v1/config/public").json()
    serialized = str(payload).casefold()
    assert payload["auth_mode"] == "anonymous"
    assert "searxng_url" not in serialized
    assert "crawl4ai_url" not in serialized
    assert "admin_ids" not in serialized
    assert "model" not in serialized


def test_anonymous_session_csrf_and_personal_boundaries(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=ImmediateRunner())
    with TestClient(app) as client:
        csrf, session = bootstrap(client)
        assert session["principal"]["authenticated"] is False
        assert session["capabilities"]["server_history"] is False

        no_origin = client.post(
            "/api/v1/chat/runs",
            json={"question": "科大有哪些校园服务？", "mode": "local"},
            headers={"X-CSRF-Token": csrf},
        )
        assert no_origin.status_code == 403
        assert no_origin.json()["error"]["code"] == "ORIGIN_MISMATCH"

        personal = client.post(
            "/api/v1/chat/runs",
            json={"question": "帮我查我的成绩", "mode": "web"},
            headers=mutation_headers(csrf),
        )
        assert personal.status_code == 401
        assert personal.json()["error"]["code"] == "AUTH_REQUIRED"

        history = client.get("/api/v1/conversations")
        assert history.status_code == 401
        assert history.json()["error"]["code"] == "AUTH_REQUIRED"


def test_demo_login_sse_history_and_cross_session_isolation(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as first, TestClient(app) as second:
        csrf, _ = bootstrap(first)
        login = first.post("/api/v1/auth/demo", headers=mutation_headers(csrf))
        assert login.status_code == 200
        login_payload = login.json()
        assert login_payload["principal"]["id"] == "PB25111691"
        assert login_payload["principal"]["profile"]["major"] == "计算机科学与技术"
        assert login_payload["principal"]["profile"]["grade"] == "2025级"
        assert login_payload["principal"]["review_namespace"] == "demo"
        assert login_payload["capabilities"]["production_publish"] is False
        csrf = login_payload["csrf_token"]

        created = first.post(
            "/api/v1/chat/runs",
            json={"question": "介绍一下本地知识库", "mode": "local"},
            headers=mutation_headers(csrf),
        )
        assert created.status_code == 200
        created_payload = created.json()
        run_id = created_payload["run_id"]
        assert created_payload["conversation_id"]

        stream = first.get("/api/v1" + created_payload["events_url"])
        assert stream.status_code == 200
        events = parse_sse(stream.text)
        assert [event["id"] for event in events] == list(range(1, len(events) + 1))
        types = [event["type"] for event in events]
        assert types[0] == "run.created"
        assert "answer.segment" in types
        assert types[-1] == "answer.completed"
        assert "thought_log" not in stream.text

        history = first.get("/api/v1/conversations").json()["items"]
        assert len(history) == 1
        detail = first.get(f"/api/v1/conversations/{history[0]['conversation_id']}").json()
        assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]

        second_csrf, _ = bootstrap(second)
        second_login = second.post("/api/v1/auth/demo", headers=mutation_headers(second_csrf)).json()
        assert second_login["principal"]["id"] == "PB25111691"
        cross_read = second.get(f"/api/v1/chat/runs/{run_id}/events")
        assert cross_read.status_code == 404
        assert cross_read.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_personal_demo_question_forces_local_mode(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        logged_in = client.post("/api/v1/auth/demo", headers=mutation_headers(csrf)).json()
        created = client.post(
            "/api/v1/chat/runs",
            json={"question": "帮我查我的课表", "mode": "web"},
            headers=mutation_headers(logged_in["csrf_token"]),
        )
        assert created.status_code == 200
        assert created.json()["effective_mode"] == "local"
        events = parse_sse(client.get("/api/v1" + created.json()["events_url"]).text)
        completed = next(event for event in events if event["type"] == "answer.completed")
        assert any("未发送到互联网" in item for item in completed["data"]["limitations"])


def test_cancel_and_last_event_id_resume(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings, runner=SlowRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        created = client.post(
            "/api/v1/chat/runs",
            json={"question": "停止测试", "mode": "local"},
            headers=mutation_headers(csrf),
        ).json()
        cancelled = client.post(
            f"/api/v1/chat/runs/{created['run_id']}/cancel",
            headers=mutation_headers(csrf),
        )
        assert cancelled.status_code == 200
        events = parse_sse(client.get("/api/v1" + created["events_url"]).text)
        assert events[-1]["type"] == "run.cancelled"

        resumed = client.get("/api/v1" + created["events_url"], headers={"Last-Event-ID": "1"})
        assert resumed.status_code == 200
        assert all(event["id"] > 1 for event in parse_sse(resumed.text))


def test_run_queue_has_a_hard_busy_boundary(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        extra={"XIAOWO_MAX_CONCURRENT_RUNS": "1", "XIAOWO_MAX_QUEUED_RUNS": "1"},
    )
    app = create_app(settings, runner=SlowRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        first = client.post(
            "/api/v1/chat/runs",
            json={"question": "占用运行位", "mode": "local"},
            headers=mutation_headers(csrf),
        )
        second = client.post(
            "/api/v1/chat/runs",
            json={"question": "占用等待位", "mode": "local"},
            headers=mutation_headers(csrf),
        )
        busy = client.post(
            "/api/v1/chat/runs",
            json={"question": "应被明确拒绝", "mode": "local"},
            headers=mutation_headers(csrf),
        )
        assert first.status_code == second.status_code == 200
        assert busy.status_code == 503
        assert busy.json()["error"]["code"] == "RUN_BUSY"
        for created in (first.json(), second.json()):
            client.post(
                f"/api/v1/chat/runs/{created['run_id']}/cancel",
                headers=mutation_headers(csrf),
            )


def test_cas_endpoints_are_disabled_outside_cas_mode(tmp_path) -> None:
    app = create_app(make_settings(tmp_path, mode="demo"), runner=ImmediateRunner())
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/api/v1/auth/cas/login")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "AUTH_MODE_DISABLED"


def test_demo_reset_only_clears_current_demo_session(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as first, TestClient(app) as second:
        first_csrf, _ = bootstrap(first)
        first_session = first.post("/api/v1/auth/demo", headers=mutation_headers(first_csrf)).json()
        second_csrf, _ = bootstrap(second)
        second_session = second.post("/api/v1/auth/demo", headers=mutation_headers(second_csrf)).json()

        for client, current_session in ((first, first_session), (second, second_session)):
            created = client.post(
                "/api/v1/chat/runs",
                json={"question": "演示恢复测试", "mode": "local"},
                headers=mutation_headers(current_session["csrf_token"]),
            ).json()
            client.get("/api/v1" + created["events_url"])

        reset = first.post(
            "/api/v1/auth/demo/reset",
            headers=mutation_headers(first_session["csrf_token"]),
        )
        assert reset.status_code == 200
        assert reset.json()["profile_id"] == "PB25111691"
        assert first.get("/api/v1/conversations").json()["items"] == []
        assert len(second.get("/api/v1/conversations").json()["items"]) == 1


def test_demo_reset_is_hidden_outside_demo_mode(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=ImmediateRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        response = client.post("/api/v1/auth/demo/reset", headers=mutation_headers(csrf))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "AUTH_MODE_DISABLED"


def test_frontend_spa_and_unknown_api_are_kept_separate(tmp_path, monkeypatch) -> None:
    import xiaowo_web.main as main_module

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><div id="root">xiaowo-spa-fixture</div>',
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "PROJECT_ROOT", tmp_path)
    app = create_app(make_settings(tmp_path), runner=ImmediateRunner())
    with TestClient(app) as client:
        spa = client.get("/academic")
        assert spa.status_code == 200
        assert "xiaowo-spa-fixture" in spa.text
        missing_api = client.get("/api/v1/not-a-real-endpoint")
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")
