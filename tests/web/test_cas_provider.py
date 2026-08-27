"""CAS Provider contract, callback state, and profile binding without real CAS."""

from __future__ import annotations

from urllib.parse import parse_qs, quote, urlsplit

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, make_settings
from xiaowo_web.auth.cas import CasIdentity
from xiaowo_web.main import create_app


class FakeCasProvider:
    def __init__(self, identity: CasIdentity | None) -> None:
        self.identity = identity
        self.login_services: list[str] = []
        self.auth_calls: list[tuple[str, str]] = []

    def login_url(self, service_url: str) -> str:
        self.login_services.append(service_url)
        return f"https://cas.example.invalid/login?service={quote(service_url, safe='')}"

    def authenticate(self, ticket: str, service_url: str) -> CasIdentity | None:
        self.auth_calls.append((ticket, service_url))
        return self.identity


def _settings(tmp_path):
    return make_settings(
        tmp_path,
        mode="cas",
        data_key="cas-test-data-key",
        public_origin="https://xiaowo.test",
        extra={
            "CAS_SERVICE_URL": "https://xiaowo.test/api/v1/auth/cas/callback",
            "XIAOWO_SESSION_SECRET": "s" * 32,
        },
    )


def test_fake_cas_login_callback_binds_profile_and_rejects_replay(tmp_path) -> None:
    provider = FakeCasProvider(CasIdentity(
        student_id="pb25111691",
        profile={"id": "PB25111691", "name": "测试", "major": "人工智能", "grade": "2025级"},
    ))
    app = create_app(_settings(tmp_path), runner=ImmediateRunner(), cas_provider=provider)
    with TestClient(app, base_url="https://xiaowo.test", follow_redirects=False) as client:
        login = client.get("/api/v1/auth/cas/login")
        assert login.status_code == 302
        assert len(provider.login_services) == 1
        service = provider.login_services[0]
        state = parse_qs(urlsplit(service).query)["state"][0]
        assert state not in login.headers["location"].split("service=")[0]

        callback = client.get(
            "/api/v1/auth/cas/callback",
            params={"ticket": "ST-fixture-once", "state": state},
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "https://xiaowo.test/academic"
        assert provider.auth_calls == [("ST-fixture-once", service)]

        session = client.get("/api/v1/auth/session").json()
        assert session["principal"]["id"] == "PB25111691"
        assert session["principal"]["profile"]["major"] == "人工智能"
        assert session["capabilities"]["personal_academic"] is True

        replay = client.get(
            "/api/v1/auth/cas/callback",
            params={"ticket": "ST-replay", "state": state},
        )
        assert replay.status_code == 403
        assert replay.json()["error"]["code"] == "CAS_STATE_INVALID"
        assert len(provider.auth_calls) == 1


def test_callback_rejects_provider_profile_id_mismatch(tmp_path) -> None:
    provider = FakeCasProvider(CasIdentity(
        student_id="PB25111691",
        profile={"id": "PB00000000", "major": "人工智能", "grade": "2025级"},
    ))
    app = create_app(_settings(tmp_path), runner=ImmediateRunner(), cas_provider=provider)
    with TestClient(app, base_url="https://xiaowo.test", follow_redirects=False) as client:
        login = client.get("/api/v1/auth/cas/login")
        state = parse_qs(urlsplit(provider.login_services[0]).query)["state"][0]
        callback = client.get(
            "/api/v1/auth/cas/callback",
            params={"ticket": "ST-mismatch", "state": state},
        )
        assert callback.status_code == 403
        assert callback.json()["error"]["code"] == "CAS_PROFILE_MISMATCH"
        assert client.get("/api/v1/auth/session").json()["principal"]["authenticated"] is False
