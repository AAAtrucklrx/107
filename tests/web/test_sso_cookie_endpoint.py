"""方案 B 端点：POST/DELETE /api/v1/auth/sso_cookie（2026-10-23）。

需要 FastAPI 环境（本地无依赖时跳过）；用 monkeypatch 替换教务探测，避免真实网络。
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from services.session_vault import SessionVault  # noqa: E402
from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers  # noqa: E402
from xiaowo_web.main import create_app  # noqa: E402

JAR = [
    {"name": "SOURCEID_TGC", "value": "T" * 64, "domain": "id.ustc.edu.cn", "path": "/"},
    {"name": "SESSION", "value": "S" * 32, "domain": "jw.ustc.edu.cn", "path": "/"},
]


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from services import cas_client as cas_mod

    monkeypatch.setattr(cas_mod.CASClient, "probe_jw_session", lambda self: True, raising=True)
    monkeypatch.setattr("services.jw_session.identity_for_client", lambda client: {})
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691", extra={
        "XIAOWO_SESSION_VAULT_KEY": "endpoint-test-key", "XIAOWO_SESSION_VAULT_TTL": "3600",
    })
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as c:
        c.app = app
        yield c


def _login(client) -> str:
    csrf, _session = bootstrap(client)
    r = client.post("/api/v1/auth/demo", headers=mutation_headers(csrf), json={})
    assert r.status_code in (200, 201)
    return r.json()["csrf_token"]


def test_import_cookie_sets_hardened_cookie(client):
    csrf = _login(client)
    r = client.post("/api/v1/auth/sso_cookie", headers=mutation_headers(csrf), json={"cookies": JAR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["cookie"] == "xiaowo_jw" and body["items"] == 2
    raw = r.headers["set-cookie"]
    assert "HttpOnly" in raw and "SameSite=lax" in raw.replace("Lax", "lax")
    # 解包校验：cookie 里确实是可用的会话
    blob = client.cookies.get("xiaowo_jw")
    items = SessionVault(client.app.state.settings.session_vault_key, ttl_seconds=3600).open(blob)
    assert {c["name"] for c in items} == {"SOURCEID_TGC", "SESSION"}


def test_reject_without_tgc_or_unusable(client):
    csrf = _login(client)
    r = client.post("/api/v1/auth/sso_cookie", headers=mutation_headers(csrf),
                    json={"cookies": [{"name": "SESSION", "value": "x", "domain": "jw.ustc.edu.cn", "path": "/"}]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "MISSING_TGC"


def test_unusable_session_rejected(monkeypatch, tmp_path):
    from services import cas_client as cas_mod

    monkeypatch.setattr(cas_mod.CASClient, "probe_jw_session", lambda self: False, raising=True)
    settings = make_settings(tmp_path, mode="demo", extra={"XIAOWO_SESSION_VAULT_KEY": "endpoint-test-key"})
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as c:
        csrf, _ = bootstrap(c)
        c.post("/api/v1/auth/demo", headers=mutation_headers(csrf), json={})
        csrf = c.get("/api/v1/auth/session").json()["csrf_token"]
        r = c.post("/api/v1/auth/sso_cookie", headers=mutation_headers(csrf), json={"cookies": JAR})
    assert r.status_code == 400 and r.json()["error"]["code"] == "COOKIE_UNUSABLE"


def test_delete_clears_cookie(client):
    csrf = _login(client)
    client.post("/api/v1/auth/sso_cookie", headers=mutation_headers(csrf), json={"cookies": JAR})
    r = client.delete("/api/v1/auth/sso_cookie", headers=mutation_headers(csrf))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "xiaowo_jw=" in r.headers.get("set-cookie", "") and "Max-Age=0" in r.headers.get("set-cookie", "")
