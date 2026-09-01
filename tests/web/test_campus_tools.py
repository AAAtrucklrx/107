"""Campus-tool submission, moderation, publication, and isolation contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.evidence.url_security import UrlGuard
from xiaowo_web.main import create_app


PUBLIC_RESOLVER = lambda _host, _port: ("93.184.216.34",)  # noqa: E731


def _demo_login(client: TestClient) -> tuple[str, dict]:
    csrf, _ = bootstrap(client)
    session = client.post(
        "/api/v1/auth/demo",
        headers=mutation_headers(csrf),
    ).json()
    return session["csrf_token"], session


def _submit(
    client: TestClient,
    csrf: str,
    *,
    url: str,
    name: str = "校历速查",
    request_id: str = "campus-tool-submit-one",
):
    return client.post(
        "/api/v1/campus/tools/applications",
        json={
            "name": name,
            "url": url,
            "description": "快速查看校历与教学周安排。",
            "category": "study",
        },
        headers={**mutation_headers(csrf), "X-Request-ID": request_id},
    )


def test_anonymous_can_browse_but_cannot_submit_tools(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=ImmediateRunner())
    app.state.campus_tool_store._url_guard = UrlGuard(resolver=PUBLIC_RESOLVER)
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        directory = client.get("/api/v1/campus/tools")
        assert directory.status_code == 200
        assert directory.json()["source"]["kind"] == "approved_community"
        assert directory.json()["items"] == []

        denied = _submit(client, csrf, url="https://example.edu/tool")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "AUTH_REQUIRED"


def test_tool_url_security_and_duplicate_boundaries(tmp_path) -> None:
    app = create_app(make_settings(tmp_path, mode="demo"), runner=ImmediateRunner())
    app.state.campus_tool_store._url_guard = UrlGuard(resolver=PUBLIC_RESOLVER)
    with TestClient(app) as client:
        csrf, _ = _demo_login(client)

        http_url = _submit(
            client,
            csrf,
            url="http://example.edu/tool",
            request_id="tool-http",
        )
        assert http_url.status_code == 422
        assert http_url.json()["error"]["code"] == "TOOL_URL_HTTPS_REQUIRED"

        private_url = _submit(
            client,
            csrf,
            url="https://127.0.0.1/tool",
            request_id="tool-private",
        )
        assert private_url.status_code == 422
        assert private_url.json()["error"]["code"] == "URL_PRIVATE_TARGET"

        sensitive_url = _submit(
            client,
            csrf,
            url="https://example.edu/tool?token=secret",
            request_id="tool-sensitive",
        )
        assert sensitive_url.status_code == 422
        assert sensitive_url.json()["error"]["code"] == "URL_SENSITIVE_QUERY"

        created = _submit(
            client,
            csrf,
            url="https://example.edu/tool?utm_source=test",
            request_id="tool-valid",
        )
        assert created.status_code == 201
        assert created.json()["normalized_url"] == "https://example.edu/tool"

        duplicate = _submit(
            client,
            csrf,
            url="https://example.edu/tool",
            request_id="tool-duplicate",
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "TOOL_URL_DUPLICATE"

        repeated = _submit(
            client,
            csrf,
            url="https://example.edu/different",
            request_id="tool-valid",
        )
        assert repeated.status_code == 201
        assert repeated.json()["application_id"] == created.json()["application_id"]


def test_demo_tool_approval_notification_and_unpublish_flow(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    app = create_app(settings, runner=ImmediateRunner())
    app.state.campus_tool_store._url_guard = UrlGuard(resolver=PUBLIC_RESOLVER)
    with TestClient(app) as client:
        csrf, session = _demo_login(client)
        assert session["principal"]["review_namespace"] == "demo"

        created = _submit(
            client,
            csrf,
            url="https://calendar.example.edu/",
            request_id="tool-flow-submit",
        )
        assert created.status_code == 201
        application = created.json()

        pending = client.get(
            "/api/v1/admin/campus-tool-applications",
            params={"status": "pending", "query": "校历"},
        ).json()
        assert pending["namespace"] == "demo"
        assert any(item["application_id"] == application["application_id"] for item in pending["items"])

        conflict = client.post(
            f"/api/v1/admin/campus-tool-applications/{application['application_id']}/approve",
            json={"expected_version": application["version"] + 1},
            headers={**mutation_headers(csrf), "X-Request-ID": "tool-flow-conflict"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "TOOL_APPLICATION_VERSION_CONFLICT"

        approved = client.post(
            f"/api/v1/admin/campus-tool-applications/{application['application_id']}/approve",
            json={"expected_version": application["version"]},
            headers={**mutation_headers(csrf), "X-Request-ID": "tool-flow-approve"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        tool_id = approved.json()["tool_id"]

        replayed = client.post(
            f"/api/v1/admin/campus-tool-applications/{application['application_id']}/approve",
            json={"expected_version": application["version"]},
            headers={**mutation_headers(csrf), "X-Request-ID": "tool-flow-approve"},
        )
        assert replayed.status_code == 200
        assert replayed.json()["tool_id"] == tool_id

        audit = client.get(
            "/api/v1/admin/campus-tool-audit",
            params={"query": application["application_id"]},
        )
        assert audit.status_code == 200
        assert audit.json()["namespace"] == "demo"
        assert [item["action"] for item in audit.json()["items"]] == [
            "application_approved",
            "application_submitted",
        ]
        assert audit.json()["items"][0]["after"]["status"] == "approved"

        directory = client.get("/api/v1/campus/tools", params={"query": "校历"}).json()
        assert directory["source"]["kind"] == "demo_fixture"
        assert [item["tool_id"] for item in directory["items"]] == [tool_id]

        mine = client.get("/api/v1/campus/tools/applications/mine").json()
        own = next(item for item in mine["items"] if item["application_id"] == application["application_id"])
        assert own["status"] == "approved"
        assert mine["unread_count"] >= 1

        notifications = client.get(
            "/api/v1/campus/tools/notifications",
            params={"unread_only": "true"},
        ).json()["items"]
        approved_notice = next(item for item in notifications if item["application_id"] == application["application_id"])
        assert approved_notice["notification_type"] == "tool_approved"
        marked = client.post(
            f"/api/v1/campus/tools/notifications/{approved_notice['notification_id']}/read",
            headers=mutation_headers(csrf),
        )
        assert marked.status_code == 200
        assert marked.json()["read_at"] is not None

        managed = client.get(
            "/api/v1/admin/campus-tools",
            params={"status": "active", "query": "校历"},
        ).json()["items"]
        tool = next(item for item in managed if item["tool_id"] == tool_id)
        unpublished = client.post(
            f"/api/v1/admin/campus-tools/{tool_id}/unpublish",
            json={"expected_version": tool["version"], "reason": "链接维护期间暂停展示。"},
            headers={**mutation_headers(csrf), "X-Request-ID": "tool-flow-unpublish"},
        )
        assert unpublished.status_code == 200
        assert unpublished.json()["status"] == "unpublished"
        assert client.get("/api/v1/campus/tools", params={"query": "校历"}).json()["items"] == []

        all_notices = client.get("/api/v1/campus/tools/notifications").json()["items"]
        assert any(
            item["notification_type"] == "tool_unpublished"
            and item["tool_id"] == tool_id
            for item in all_notices
        )


def test_rejection_requires_reason_and_new_application_keeps_history(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    app = create_app(settings, runner=ImmediateRunner())
    app.state.campus_tool_store._url_guard = UrlGuard(resolver=PUBLIC_RESOLVER)
    with TestClient(app) as client:
        csrf, _ = _demo_login(client)
        first = _submit(
            client,
            csrf,
            url="https://clubs.example.edu/",
            name="社团查询",
            request_id="tool-reject-submit",
        ).json()

        missing_reason = client.post(
            f"/api/v1/admin/campus-tool-applications/{first['application_id']}/reject",
            json={"expected_version": first["version"], "reason": ""},
            headers=mutation_headers(csrf),
        )
        assert missing_reason.status_code == 422

        rejected = client.post(
            f"/api/v1/admin/campus-tool-applications/{first['application_id']}/reject",
            json={"expected_version": first["version"], "reason": "说明不足，无法确认具体用途。"},
            headers={**mutation_headers(csrf), "X-Request-ID": "tool-reject-decision"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["decision_reason"] == "说明不足，无法确认具体用途。"

        resubmitted = _submit(
            client,
            csrf,
            url="https://clubs.example.edu/",
            name="社团活动查询",
            request_id="tool-reject-resubmit",
        )
        assert resubmitted.status_code == 201
        assert resubmitted.json()["application_id"] != first["application_id"]

        mine = client.get("/api/v1/campus/tools/applications/mine").json()["items"]
        statuses = {item["application_id"]: item["status"] for item in mine}
        assert statuses[first["application_id"]] == "rejected"
        assert statuses[resubmitted.json()["application_id"]] == "pending"


def test_demo_and_production_tool_namespaces_are_isolated(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    app = create_app(settings, runner=ImmediateRunner())
    store = app.state.campus_tool_store
    store._url_guard = UrlGuard(resolver=PUBLIC_RESOLVER)
    with TestClient(app) as client:
        csrf, _ = _demo_login(client)
        demo_created = _submit(
            client,
            csrf,
            url="https://shared.example.edu/",
            request_id="demo-isolation-submit",
        ).json()
        store.approve_application(
            "demo",
            demo_created["application_id"],
            expected_version=demo_created["version"],
            actor_key="PB25111691",
            request_id="demo-isolation-approve",
        )

        production = store.submit_application(
            namespace="production",
            applicant_principal_id="PB00000001",
            applicant_auth_mode="cas",
            applicant_name="生产用户",
            name="生产工具",
            description="只属于生产命名空间。",
            category="other",
            url="https://shared.example.edu/",
            request_id="production-isolation-submit",
        )
        store.approve_application(
            "production",
            production["application_id"],
            expected_version=production["version"],
            actor_key="PB00000099",
            request_id="production-isolation-approve",
        )

        demo_items = client.get("/api/v1/campus/tools", params={"query": "shared"}).json()["items"]
        production_items = store.list_public_tools("production", query="shared")
        assert len(demo_items) == len(production_items) == 1
        assert demo_items[0]["name"] == "校历速查"
        assert production_items[0]["name"] == "生产工具"
        assert demo_items[0]["tool_id"] != production_items[0]["tool_id"]


def test_admin_idempotency_key_cannot_be_reused_for_another_application(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    app = create_app(settings, runner=ImmediateRunner())
    app.state.campus_tool_store._url_guard = UrlGuard(resolver=PUBLIC_RESOLVER)
    with TestClient(app) as client:
        csrf, _ = _demo_login(client)
        first = _submit(
            client,
            csrf,
            url="https://first.example.edu/",
            name="第一个工具",
            request_id="tool-idempotency-first-submit",
        ).json()
        second = _submit(
            client,
            csrf,
            url="https://second.example.edu/",
            name="第二个工具",
            request_id="tool-idempotency-second-submit",
        ).json()

        approved = client.post(
            f"/api/v1/admin/campus-tool-applications/{first['application_id']}/approve",
            json={"expected_version": first["version"]},
            headers={**mutation_headers(csrf), "X-Request-ID": "shared-admin-decision"},
        )
        assert approved.status_code == 200

        conflict = client.post(
            f"/api/v1/admin/campus-tool-applications/{second['application_id']}/approve",
            json={"expected_version": second["version"]},
            headers={**mutation_headers(csrf), "X-Request-ID": "shared-admin-decision"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "TOOL_REQUEST_ID_CONFLICT"
        detail = client.get(
            f"/api/v1/admin/campus-tool-applications/{second['application_id']}"
        ).json()
        assert detail["status"] == "pending"
