"""会话保险箱（方案 B）：加解密往返、篡改拒绝、过期、精简体量（2026-10-23）。"""
from __future__ import annotations

import pytest

from services.session_vault import (
    COOKIE_NAME, CookiePayloadError, SessionVault, apply_cookies, validate_payload,
)

COOKIES = [
    {"name": "SOURCEID_TGC", "value": "T" * 552, "domain": "id.ustc.edu.cn", "path": "/"},
    {"name": "SESSION", "value": "s" * 36, "domain": "jw.ustc.edu.cn", "path": "/"},
    {"name": "AG_session", "value": "a" * 1472, "domain": "analytics.ustc.edu.cn", "path": "/"},
]


def _vault(secret: str = "unit-test-key") -> SessionVault:
    return SessionVault(secret, ttl_seconds=600)


def test_roundtrip_keeps_essential_only():
    vault = _vault()
    blob = vault.seal(COOKIES)
    out = vault.open(blob)
    assert out is not None
    names = {c["name"] for c in out}
    assert names == {"SOURCEID_TGC", "SESSION"}          # analytics 被丢弃
    assert len(blob) < 1500                              # 远低于 4KB cookie 上限


def test_tampered_blob_rejected():
    vault = _vault()
    blob = vault.seal(COOKIES)
    bad = blob[:-2] + ("AA" if not blob.endswith("AA") else "BB")
    assert vault.open(bad) is None


def test_wrong_key_rejected():
    blob = _vault("key-a").seal(COOKIES)
    assert _vault("key-b").open(blob) is None


def test_expired_session_rejected():
    vault = _vault()
    blob = vault.seal(COOKIES, now=1_000_000)
    assert vault.open(blob, now=1_000_000 + 601) is None   # 超过 TTL
    assert vault.open(blob, now=1_000_000 + 60) is not None


def test_future_issued_at_rejected():
    vault = _vault()
    blob = vault.seal(COOKIES, now=1_000_000)
    assert vault.open(blob, now=1_000_000 - 3600) is None   # 时钟倒流 1 小时


def test_disabled_without_secret():
    vault = SessionVault("")
    assert vault.enabled is False
    with pytest.raises(RuntimeError):
        vault.seal(COOKIES)
    assert vault.open("whatever") is None


def test_cookie_attributes_are_hardened():
    kwargs = _vault().cookie_kwargs()
    assert kwargs["httponly"] is True and kwargs["secure"] is True
    assert kwargs["samesite"] == "lax" and kwargs["path"] == "/"
    assert COOKIE_NAME == "xiaowo_jw"


def test_apply_cookies_populates_session():
    import requests

    vault = _vault()
    session = requests.Session()
    assert apply_cookies(session, vault.open(vault.seal(COOKIES))) == 2
    assert {c.name for c in session.cookies} == {"SOURCEID_TGC", "SESSION"}


def test_validate_payload_accepts_real_jar():
    items = validate_payload(COOKIES)
    assert {c["name"] for c in items} == {"SOURCEID_TGC", "SESSION"}


def test_validate_payload_rejects_bad_shapes():
    for bad in ([], None, "x", [{"nope": 1}]):
        with pytest.raises(CookiePayloadError) as exc:
            validate_payload(bad)
        assert exc.value.code == "INVALID_COOKIES"


def test_validate_payload_requires_tgc():
    jar = [{"name": "SESSION", "value": "x", "domain": "jw.ustc.edu.cn", "path": "/"}]
    with pytest.raises(CookiePayloadError) as exc:
        validate_payload(jar)
    assert exc.value.code == "MISSING_TGC"
