"""请求级教务会话（方案 B）：cookie 解包 → CASClient 注入（2026-10-23）。"""
from __future__ import annotations

import pytest

from services.jw_session import (
    client_from_cookie_blob, current_client, reset_client, set_client,
)
from services.session_vault import SessionVault

COOKIES = [
    {"name": "SOURCEID_TGC", "value": "T" * 32, "domain": "id.ustc.edu.cn", "path": "/"},
    {"name": "SESSION", "value": "S" * 32, "domain": "jw.ustc.edu.cn", "path": "/"},
]


class _Settings:
    session_vault_key = "unit-test-key"
    session_vault_ttl_seconds = 600


def test_blob_builds_client_with_cookies():
    vault = SessionVault(_Settings.session_vault_key, ttl_seconds=600)
    client = client_from_cookie_blob(vault.seal(COOKIES), settings=_Settings)
    assert client is not None
    assert {c.name for c in client.session.cookies} == {"SOURCEID_TGC", "SESSION"}
    assert client.probe_jw_session  # 方法存在（未调用，不触网）


def test_missing_or_invalid_blob_returns_none():
    assert client_from_cookie_blob(None, settings=_Settings) is None
    assert client_from_cookie_blob("garbage", settings=_Settings) is None


def test_disabled_vault_returns_none():
    class _Disabled:
        session_vault_key = ""
        session_vault_ttl_seconds = 600

    vault = SessionVault("k", ttl_seconds=600)
    assert client_from_cookie_blob(vault.seal(COOKIES), settings=_Disabled) is None


def test_contextvar_set_and_reset():
    sentinel = object()
    token = set_client(sentinel)
    try:
        assert current_client() is sentinel
    finally:
        reset_client(token)
    assert current_client() is None


def test_cas_prefers_request_client(monkeypatch):
    from tools import course_tools

    sentinel = object()
    token = set_client(sentinel)
    try:
        assert course_tools._cas() is sentinel
    finally:
        reset_client(token)
