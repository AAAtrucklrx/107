"""Mobile bearer contracts; all databases and school responses are synthetic."""
from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.main import create_app

ORIGIN = "https://localhost"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
JAR = [
    {"name": "SOURCEID_TGC", "value": "synthetic-school-secret", "domain": "id.ustc.edu.cn", "path": "/"},
    {"name": "SESSION", "value": "synthetic-jw-secret", "domain": "jw.ustc.edu.cn", "path": "/"},
]


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, public_origin="http://127.0.0.1:8000", data_key="synthetic-history-encryption-key", extra={
        "XIAOWO_MOBILE_AUTH_ENABLED": "true",
        "XIAOWO_MOBILE_DEBUG_LOOPBACK": "true",
        "XIAOWO_MOBILE_AUTH_DB_PATH": str(tmp_path / "mobile-auth.db"),
        "XIAOWO_MOBILE_VAULT_KEY": "synthetic-mobile-test-key-at-least-32-bytes",
    })


@pytest.fixture
def client(settings, monkeypatch):
    from services.cas_client import CASClient
    import services.jw_session as jw
    import requests
    def no_network(*_args, **_kwargs):
        raise AssertionError("test attempted real school/network request")
    monkeypatch.setattr(requests.Session, "request", no_network)
    monkeypatch.setattr(CASClient, "probe_jw_session", lambda self: True)
    def school_identity(client):
        client._student_data_id = 260001
        return {"student_id": "PB26000001", "name": "合成同学", "major": "数学", "grade": "2026级"}
    monkeypatch.setattr(jw, "identity_for_client", school_identity)
    with TestClient(create_app(settings, runner=ImmediateRunner())) as c:
        yield c


def authorize(client, **changes):
    body = {"cookies": JAR, "code_challenge": CHALLENGE, "code_challenge_method": "S256", **changes}
    return client.post("/api/v1/auth/mobile/authorize", headers={"Origin": ORIGIN}, json=body)


def exchange(client, code, verifier=VERIFIER):
    return client.post("/api/v1/auth/mobile/exchange", headers={"Origin": ORIGIN},
                       json={"code": code, "code_verifier": verifier})


def login(client):
    grant = authorize(client)
    assert grant.status_code == 200, grant.text
    response = exchange(client, grant.json()["code"])
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def headers(token):
    return {"Authorization": f"Bearer {token}", "Origin": ORIGIN}


def test_bootstrap_uses_bearer_without_cookies_or_personal_capabilities(client):
    response = client.post("/api/v1/auth/mobile/bootstrap", headers={"Origin": ORIGIN}, json={})
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    token = response.json()["access_token"]
    assert len(token) == 43
    session = client.get("/api/v1/auth/session", headers=headers(token))
    assert session.status_code == 200
    assert "set-cookie" not in session.headers
    assert session.json()["principal"]["authenticated"] is False
    assert session.json()["capabilities"]["personal_academic"] is False
    assert client.get("/api/v1/academic/courses", headers=headers(token)).status_code == 401


def test_exchange_binds_school_identity_and_rejects_replay(client):
    grant = authorize(client)
    assert grant.status_code == 200, grant.text
    code = grant.json()["code"]
    assert exchange(client, code, "w" * 64).status_code == 400
    response = exchange(client, code)
    assert response.status_code == 200
    assert response.json()["expires_in"] <= 43200
    assert "set-cookie" not in response.headers
    token = response.json()["access_token"]
    session = client.get("/api/v1/auth/session", headers=headers(token))
    assert session.json()["principal"]["id"] == "PB26000001"
    assert session.json()["jw_identity"]["name"] == "合成同学"
    assert session.json()["jw_session"] is True
    assert exchange(client, code).status_code == 400


@pytest.mark.parametrize("probe_status, expected_status", [(200, 200), (302, 401), (503, 503)])
def test_authorize_uses_real_cas_identity_only_after_successful_probe(settings, monkeypatch, probe_status, expected_status):
    """Exercise CASClient's login gate and identity parser, replacing only HTTP."""
    import json
    import requests
    import services.jw_session as jw

    monkeypatch.setattr(jw, "_identity_cache", {})
    base = "https://jw.ustc.edu.cn"
    menu_url = base + "/home/menu"
    semesters_url = base + "/for-std/grade/sheet/getSemesters"
    grades_url = base + "/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds=461"
    profile_url = base + "/for-std/student-info/info/260001"
    course_url = base + "/for-std/course-table/get-data?bizTypeId=2&semesterId=461&dataId=260001"
    responses = {
        menu_url: "<html>synthetic authenticated school menu</html>",
        semesters_url: [{"id": 461}],
        grades_url: {
            "semesters": [{"scores": [{"studentAssoc": 260001}]}],
            "stdGradeRank": {"studentCode": "PB26000001", "studentName": "合成同学"},
        },
        profile_url: "<dl><dt>学号</dt><dd>PB26000001</dd><dt>姓名</dt><dd>合成同学</dd>"
                     "<dt>所在学院</dt><dd>合成学院</dd><dt>所修专业</dt><dd>数学</dd>"
                     "<dt>年级</dt><dd>2026级</dd></dl>",
        course_url: {"lessons": [{"name": "合成课程"}]},
    }
    requested = []

    def school_http(_session, request, **kwargs):
        requested.append(request.url)
        assert request.method == "GET"
        assert request.url in responses, "unexpected school request"
        assert "SESSION=synthetic-jw-secret" in request.headers.get("Cookie", "")
        response = requests.Response()
        response.request, response.url = request, request.url
        response.status_code = probe_status if request.url == menu_url else 200
        if request.url == menu_url:
            assert kwargs["allow_redirects"] is False
            if probe_status == 302:
                response.headers["Location"] = "https://id.ustc.edu.cn/cas/login"
        payload = responses[request.url]
        response._content = (payload if isinstance(payload, str) else json.dumps(payload)).encode("utf-8")
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "send", school_http)
    with TestClient(create_app(settings, runner=ImmediateRunner())) as real_client:
        grant = authorize(real_client)
        assert grant.status_code == expected_status, grant.text
        assert requested[0] == menu_url
        if probe_status != 200:
            assert requested == [menu_url]
            return
        assert semesters_url in requested and grades_url in requested and profile_url in requested
        response = exchange(real_client, grant.json()["code"])
        assert response.status_code == 200, response.text
        principal = real_client.app.state.mobile_auth.resolve(response.json()["access_token"])
        assert principal.principal_id == "PB26000001"
        assert principal.profile["name"] == "合成同学"
        assert principal.profile["college"] == "合成学院"
        assert principal.profile["major"] == "数学"
        assert "student_data_id" not in principal.profile

        from services.cas_client import CASClient
        from services.session_vault import apply_cookies
        cached_client = CASClient()
        try:
            apply_cookies(cached_client.session, JAR)
            cached_client._logged_in = True
            request_count = len(requested)
            identity = jw.identity_for_client(cached_client)
            assert cached_client.student_data_id == 260001
            assert "student_data_id" not in identity
            assert len(requested) == request_count, "cache restoration must not add school requests"
        finally:
            cached_client.session.close()

        class SchoolCourses:
            def courses(self, _principal):
                return jw.current_client().get_course_table(461)
        real_client.app.state.academic_service = SchoolCourses()
        courses = real_client.get("/api/v1/academic/courses", headers=headers(response.json()["access_token"]))
        assert courses.status_code == 200, courses.text
        assert courses.json() == responses[course_url]
        assert requested[-1] == course_url


def test_verified_identity_reaches_academic_client_without_cookie_fallback(client):
    from services.jw_session import current_client
    class Inspector:
        def courses(self, principal):
            school = current_client()
            return {"school_student": school.student_id, "principal_student": principal.principal_id}
    client.app.state.academic_service = Inspector()
    token = login(client)
    response = client.get("/api/v1/academic/courses", headers=headers(token))
    assert response.json() == {"school_student": "PB26000001", "principal_student": "PB26000001"}


@pytest.mark.parametrize("boundary", ["tool", "school_http"])
def test_revoked_inflight_request_cannot_start_more_work(client, monkeypatch, boundary):
    """Revocation must reach already-copied contexts, not only future requests."""
    from services.jw_session import current_client, in_thread_context
    import requests
    attempted = []
    token = login(client)

    def synthetic_send(_session, _request, **_kwargs):
        attempted.append("school_http")
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(requests.Session, "send", synthetic_send)

    class Inspector:
        def courses(self, _principal):
            later_tool = in_thread_context(lambda: attempted.append("tool"))
            school = current_client()
            client.app.state.mobile_auth.revoke(token)
            if boundary == "tool":
                later_tool()
            else:
                prepared = requests.Request("GET", "https://jw.ustc.edu.cn/synthetic").prepare()
                school.session.send(prepared)
            return {"continued": True}

    client.app.state.academic_service = Inspector()
    response = client.get("/api/v1/academic/courses", headers=headers(token))
    assert response.status_code == 401
    assert attempted == []


def test_revocation_between_middleware_and_dependency_rejects_cached_principal(client):
    from starlette.requests import Request
    from xiaowo_web.api.dependencies import optional_principal
    from xiaowo_web.errors import ApiError
    token = login(client)
    request = Request({"type": "http", "app": client.app,
                       "headers": [(b"authorization", f"Bearer {token}".encode())]})
    request.state.mobile_principal = client.app.state.mobile_auth.resolve(token)
    client.app.state.mobile_auth.revoke(token)
    with pytest.raises(ApiError) as caught:
        optional_principal(request)
    assert caught.value.status_code == 401


def test_concurrent_exchange_has_exactly_one_winner(client):
    grant = authorize(client)
    assert grant.status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: exchange(client, grant.json()["code"]), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 400]


def test_invalid_bearer_never_falls_back_to_valid_browser_cookie(client):
    bootstrap(client)
    response = client.get("/api/v1/auth/session", headers=headers("invalid"))
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("origin", [None, "null", "https://localhost.evil", "http://localhost"])
def test_mobile_auth_rejects_untrusted_origins(client, origin):
    response = client.post("/api/v1/auth/mobile/bootstrap", headers={} if origin is None else {"Origin": origin}, json={})
    assert response.status_code == 403


def test_origin_cors_and_browser_csrf_remain_separate(client):
    token = login(client)
    assert client.post("/api/v1/chat/runs", headers=headers(token), json={"question": "图书馆在哪", "mode": "local"}).status_code == 200
    wrong = {**headers(token), "Origin": "https://evil.example"}
    assert client.post("/api/v1/chat/runs", headers=wrong, json={"question": "图书馆在哪"}).status_code == 403
    allowed = client.options("/api/v1/chat/runs", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type,last-event-id"})
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    denied = client.options("/api/v1/chat/runs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in denied.headers
    csrf, _ = bootstrap(client)
    assert client.post("/api/v1/chat/runs", headers={"Origin": "http://127.0.0.1:8000"}, json={"question": "校车在哪"}).status_code == 403
    assert client.post("/api/v1/chat/runs", headers=mutation_headers(csrf, "http://127.0.0.1:8000"), json={"question": "校车在哪"}).status_code == 200


@pytest.mark.parametrize("endpoint,method", [("/auth/logout", "post"), ("/auth/sso_cookie", "delete")])
def test_logout_revokes_token_and_credentials_idempotently(client, endpoint, method):
    token = login(client)
    for _ in range(2):
        response = getattr(client, method)("/api/v1" + endpoint, headers=headers(token))
        assert response.status_code == 200
    assert client.get("/api/v1/auth/session", headers=headers(token)).status_code == 401
    assert client.app.state.mobile_auth.resolve(token) is None


def test_school_validation_cannot_accept_supplied_identity(client):
    assert authorize(client, student_id="PB99999999").status_code == 422
    assert authorize(client, student_data_id=999999).status_code == 422
    assert authorize(client, cookies=[{"name": "SOURCEID_TGC", "value": "x", "domain": "evil.example", "path": "/"}]).status_code == 400
    assert authorize(client, cookies=[{**JAR[0], "value": "x" * 40000}]).status_code in {413, 422}


def test_mobile_disabled_by_default(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), runner=ImmediateRunner())) as c:
        assert c.post("/api/v1/auth/mobile/bootstrap", headers={"Origin": ORIGIN}, json={}).status_code == 404


def test_credentials_are_encrypted_recoverable_and_bound_to_record(client, settings):
    from xiaowo_web.auth.mobile import MobileAuth
    import sqlite3
    token = login(client)
    recovered = MobileAuth(settings)
    recovered.initialize()
    assert recovered.resolve(token).principal_id == "PB26000001"
    assert MobileAuth(replace(settings, mobile_vault_key="wrong-key-at-least-32-bytes-long!!")).resolve(token) is None
    with sqlite3.connect(settings.mobile_auth_db_path) as db:
        rows = db.execute("SELECT token_hash, session_id, sealed FROM mobile_sessions").fetchall()
        all_values = str(rows)
        assert token not in all_values
        assert "synthetic-school-secret" not in all_values
        assert "合成同学" not in all_values
        db.execute("UPDATE mobile_sessions SET session_id='mobile:tampered'")
    assert recovered.resolve(token) is None


def test_grant_and_token_absolute_expiry(client, monkeypatch):
    import xiaowo_web.auth.mobile as mobile
    now = mobile.time.time()
    grant = authorize(client)
    assert grant.status_code == 200
    token = login(client)
    monkeypatch.setattr(mobile.time, "time", lambda: now + 61)
    assert exchange(client, grant.json()["code"]).status_code == 400
    import sqlite3
    with sqlite3.connect(client.app.state.settings.mobile_auth_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM mobile_grants").fetchone()[0] == 0
    monkeypatch.setattr(mobile.time, "time", lambda: now + 43201)
    assert client.get("/api/v1/auth/session", headers=headers(token)).status_code == 401
    with sqlite3.connect(client.app.state.settings.mobile_auth_db_path) as db:
        assert db.execute("SELECT sealed FROM mobile_sessions").fetchone()[0] is None


def test_missing_school_identity_does_not_create_grant(client, monkeypatch):
    import services.jw_session as jw
    monkeypatch.setattr(jw, "identity_for_client", lambda _: {"name": "无学号"})
    assert authorize(client).status_code == 401


@pytest.mark.parametrize("data_id", [None, True, 0, -1, "260001"])
def test_authorize_requires_a_verified_positive_school_data_id(client, monkeypatch, data_id):
    import services.jw_session as jw
    def incomplete_identity(school):
        school._student_data_id = data_id
        return {"student_id": "PB26000001", "name": "合成同学"}
    monkeypatch.setattr(jw, "identity_for_client", incomplete_identity)
    assert authorize(client).status_code == 401


def test_legacy_mobile_payload_without_data_id_requires_login_without_cookie_fallback(client):
    import time
    bootstrap(client)  # A valid browser cookie must not rescue the obsolete bearer.
    store = client.app.state.mobile_auth
    payload = {"principal_id": "PB26000001", "auth_mode": "cas", "cookies": JAR,
               "profile": {"id": "PB26000001", "name": "合成同学"}}
    with store._connect() as connection:
        token = store._insert_session(connection, payload, expires_at=time.time() + 600)["access_token"]
    assert client.get("/api/v1/auth/session", headers=headers(token)).status_code == 401
    anonymous = client.post("/api/v1/auth/mobile/bootstrap", headers={"Origin": ORIGIN}, json={})
    assert anonymous.status_code == 200
    assert client.get("/api/v1/auth/session", headers=headers(anonymous.json()["access_token"])).status_code == 200
    assert client.get("/api/v1/auth/session", headers=headers(login(client))).status_code == 200


def test_restoring_token_with_expired_school_session_requires_login(client, monkeypatch):
    from services.cas_client import CASClient
    token = login(client)
    monkeypatch.setattr(CASClient, "probe_jw_session", lambda self: False)
    assert client.get("/api/v1/auth/session", headers=headers(token)).status_code == 401
    assert client.app.state.mobile_auth.resolve(token) is None


def test_school_network_failure_preserves_existing_token(client, monkeypatch):
    from services.cas_client import CASClient
    token = login(client)
    def unavailable(self):
        self._probe_unavailable = True
        return False
    monkeypatch.setattr(CASClient, "probe_jw_session", unavailable)
    assert client.get("/api/v1/auth/session", headers=headers(token)).status_code == 503
    assert client.app.state.mobile_auth.resolve(token) is not None
    assert authorize(client).status_code == 503


def test_bearer_cannot_create_browser_cookie_sessions(client):
    token = login(client)
    response = client.post("/api/v1/auth/sso_cookie", headers=headers(token), json={"cookies": JAR})
    assert response.status_code == 409
    assert "set-cookie" not in response.headers


def test_bearer_never_enters_demo_cookie_login(tmp_path):
    settings = make_settings(tmp_path, mode="demo", public_origin="http://127.0.0.1:8000", data_key="synthetic-history-encryption-key", extra={
        "XIAOWO_MOBILE_AUTH_ENABLED": "true", "XIAOWO_MOBILE_DEBUG_LOOPBACK": "true",
        "XIAOWO_MOBILE_AUTH_DB_PATH": str(tmp_path / "mobile.db"), "XIAOWO_MOBILE_VAULT_KEY": "k" * 40,
    })
    with TestClient(create_app(settings, runner=ImmediateRunner())) as c:
        token = c.post("/api/v1/auth/mobile/bootstrap", headers={"Origin": ORIGIN}, json={}).json()["access_token"]
        response = c.post("/api/v1/auth/demo", headers=headers(token), json={})
        assert response.status_code == 409
        assert "set-cookie" not in response.headers


def test_mobile_auth_rate_limit_and_json_only(client):
    assert client.post("/api/v1/auth/mobile/bootstrap", headers={"Origin": ORIGIN}, content="{}").status_code == 415
    statuses = [client.post("/api/v1/auth/mobile/bootstrap", headers={"Origin": ORIGIN}, json={}).status_code for _ in range(21)]
    assert statuses[-1] == 429


def test_mobile_configuration_fails_closed(tmp_path):
    from xiaowo_web.settings import SettingsError
    for extra in (
        {"XIAOWO_MOBILE_AUTH_ENABLED": "true"},
        {"XIAOWO_MOBILE_AUTH_ENABLED": "true", "XIAOWO_MOBILE_VAULT_KEY": "k" * 40},
        {"XIAOWO_MOBILE_AUTH_ENABLED": "true", "XIAOWO_MOBILE_VAULT_KEY": "k" * 40, "XIAOWO_MOBILE_DEBUG_LOOPBACK": "true"},
    ):
        with pytest.raises(SettingsError):
            make_settings(tmp_path, public_origin="http://example.com", extra=extra)


def test_mobile_real_identity_requires_encrypted_personal_history(settings):
    from xiaowo_web.settings import SettingsError
    with pytest.raises(SettingsError):
        replace(settings, data_key="").validate()


def test_sse_requires_owner_and_stops_after_revocation(client):
    import asyncio
    from types import SimpleNamespace
    from xiaowo_web.api.routers.chat import _event_stream
    token = login(client)
    principal = client.app.state.mobile_auth.resolve(token)
    run = client.app.state.store.create_run(principal.session_key, "local")
    client.app.state.store.append_event(run.run_id, "answer.delta", {"delta": "first"})
    other_token = login(client)
    assert client.get(f"/api/v1/chat/runs/{run.run_id}/events", headers=headers(other_token)).status_code == 404
    async def connected():
        return False
    request = SimpleNamespace(app=client.app, state=SimpleNamespace(mobile_token=token, mobile_principal=principal), is_disconnected=connected)
    async def stream_then_revoke():
        stream = _event_stream(request, run.run_id, 0)
        first = await anext(stream)
        assert "first" in first
        client.app.state.mobile_auth.revoke(token)
        client.app.state.store.append_event(run.run_id, "answer.delta", {"delta": "must-not-leak"})
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    asyncio.run(stream_then_revoke())
