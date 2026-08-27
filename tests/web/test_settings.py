"""Startup configuration must fail closed for unsafe combinations."""

from __future__ import annotations

import pytest

from xiaowo_web.settings import SettingsError, WebSettings


def _base(**updates: str) -> dict[str, str]:
    values = {
        "XIAOWO_ENV": "development",
        "XIAOWO_AUTH_MODE": "anonymous",
        "XIAOWO_PUBLIC_ORIGIN": "http://localhost:8000",
    }
    values.update(updates)
    return values


@pytest.mark.parametrize(
    ("key", "value"),
    [("XIAOWO_ENV", "preview"), ("XIAOWO_AUTH_MODE", "password")],
)
def test_unknown_enums_are_rejected(key: str, value: str) -> None:
    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(**{key: value}))


def test_production_demo_is_rejected() -> None:
    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(XIAOWO_ENV="production", XIAOWO_AUTH_MODE="demo"))


def test_anonymous_admin_is_rejected() -> None:
    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(XIAOWO_ADMIN_IDS="PB25111691"))


def test_demo_only_accepts_the_single_fixture_admin() -> None:
    with pytest.raises(SettingsError):
        WebSettings.from_env(
            _base(XIAOWO_AUTH_MODE="demo", XIAOWO_ADMIN_IDS="PB00000000"),
        )


def test_cas_requires_https_matching_origin_and_secrets() -> None:
    common = {
        "XIAOWO_AUTH_MODE": "cas",
        "XIAOWO_PUBLIC_ORIGIN": "https://xiaowo.example.edu",
        "CAS_SERVICE_URL": "https://xiaowo.example.edu/api/v1/auth/cas/callback",
        "XIAOWO_DATA_KEY": "data-key",
        "XIAOWO_SESSION_SECRET": "s" * 32,
    }
    settings = WebSettings.from_env(_base(**common))
    assert settings.cookie_secure is True

    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(**{**common, "XIAOWO_PUBLIC_ORIGIN": "http://xiaowo.example.edu"}))
    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(**{**common, "CAS_SERVICE_URL": "https://other.example.edu/callback"}))
    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(**{**common, "XIAOWO_SESSION_SECRET": "short"}))


def test_invalid_proxy_and_unapproved_sidecar_are_rejected() -> None:
    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(XIAOWO_TRUSTED_PROXY_CIDRS="not-a-network"))
    with pytest.raises(SettingsError):
        WebSettings.from_env(
            _base(
                XIAOWO_WEB_SEARCH_ENABLED="true",
                XIAOWO_SEARXNG_URL="https://public.example/search",
            ),
        )


def test_numeric_configuration_errors_are_stable() -> None:
    with pytest.raises(SettingsError):
        WebSettings.from_env(_base(XIAOWO_MAX_CONCURRENT_RUNS="many"))


def test_evidence_extractor_reuses_the_configured_llm_unless_overridden() -> None:
    defaulted = WebSettings.from_env(_base())
    assert defaulted.evidence_extractor_model == "deepseek-v4-flash"

    inherited = WebSettings.from_env(_base(LLM_MODEL="fixture-chat-model"))
    assert inherited.evidence_extractor_model == "fixture-chat-model"

    overridden = WebSettings.from_env(_base(
        LLM_MODEL="fixture-chat-model",
        XIAOWO_EVIDENCE_EXTRACTOR_MODEL="fixture-evidence-model",
    ))
    assert overridden.evidence_extractor_model == "fixture-evidence-model"
