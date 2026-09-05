"""Fixed security fixtures for privacy, SSRF, trust, and evidence independence."""

from __future__ import annotations

import pytest

from xiaowo_web.evidence.gate import assess_claim
from xiaowo_web.evidence.models import EvidenceSource
from xiaowo_web.evidence.privacy import QuerySafetyError, sanitize_public_query
from xiaowo_web.evidence.trust import SourceTrustStore
from xiaowo_web.evidence.url_security import UrlGuard, UrlSafetyError


def _public_resolver(_host: str, _port: int):
    return ["8.8.8.8"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://user:password@example.com/",
        "ftp://example.com/file",
    ],
)
def test_url_guard_blocks_private_and_ambiguous_targets(url: str) -> None:
    with pytest.raises(UrlSafetyError):
        UrlGuard(_public_resolver).validate(url)


def test_url_guard_blocks_mixed_dns_and_sensitive_parameters() -> None:
    mixed = UrlGuard(lambda _host, _port: ["8.8.8.8", "10.0.0.5"])
    with pytest.raises(UrlSafetyError) as private_error:
        mixed.validate("https://example.com/news")
    assert private_error.value.code == "URL_PRIVATE_TARGET"

    guard = UrlGuard(_public_resolver)
    with pytest.raises(UrlSafetyError) as query_error:
        guard.validate("https://example.com/?ticket=ST-secret-value")
    assert query_error.value.code == "URL_SENSITIVE_QUERY"


def test_url_guard_normalizes_tracking_and_ustc_tag() -> None:
    result = UrlGuard(_public_resolver).validate(
        "https://WWW.TEACH.USTC.EDU.CN:443/news/?utm_source=x&id=4#fragment",
    )
    assert result.normalized_url == "https://www.teach.ustc.edu.cn/news/?id=4"
    assert result.ustc_domain is True
    assert result.approved_ips == ("8.8.8.8",)


def test_unknown_ustc_subdomain_is_not_promoted_to_official() -> None:
    guard = UrlGuard(_public_resolver)
    trust = SourceTrustStore()
    official = trust.classify(guard.validate("https://www.teach.ustc.edu.cn/notice/1"))
    unknown = trust.classify(guard.validate("https://unknown.ustc.edu.cn/notice/1"))
    assert official.level == "official_primary"
    assert unknown.level == "general"
    assert unknown.tags == ("ustc_domain",)


def test_query_sanitizer_rejects_personal_and_credentials() -> None:
    with pytest.raises(QuerySafetyError) as personal:
        sanitize_public_query("帮我查我的成绩")
    assert personal.value.code == "PERSONAL_QUERY"
    with pytest.raises(QuerySafetyError) as credential:
        sanitize_public_query("查询 ticket=ST-super-secret 的状态")
    assert credential.value.code == "WEB_QUERY_UNSAFE"


def test_query_sanitizer_removes_bound_identity_without_history() -> None:
    sanitized = sanitize_public_query(
        "测试想查中国科大图书馆今天开放吗",
        {"name": "测试", "id": "PB25111691"},
    )
    assert "测试" not in sanitized.text
    assert "PB25111691" not in sanitized.text
    assert "中国科大图书馆今天开放吗" in sanitized.text
    assert len(sanitized.digest) == 64


def _source(
    source_id: str,
    *,
    level: str,
    relation: str = "supports",
    domain: str | None = None,
    content_hash: str | None = None,
) -> EvidenceSource:
    return EvidenceSource(
        source_id=source_id,
        normalized_url=f"https://{domain or source_id}.example/{source_id}",
        registered_domain=domain or f"{source_id}.example",
        level=level,
        relation=relation,
        content_hash=content_hash or f"hash-{source_id}",
        near_duplicate_hash=f"near-{source_id}",
    )


def test_one_official_source_confirms_claim() -> None:
    result = assess_claim([_source("official", level="official_primary")])
    assert result.status == "confirmed"
    assert result.supporting_source_ids == ("official",)


def test_two_reliable_sources_must_be_independent() -> None:
    independent = assess_claim([
        _source("a", level="reliable_independent", domain="alpha.example"),
        _source("b", level="reliable_independent", domain="beta.example"),
    ])
    same_domain = assess_claim([
        _source("a", level="reliable_independent", domain="same.example"),
        _source("b", level="reliable_independent", domain="same.example"),
    ])
    same_body = assess_claim([
        _source("a", level="reliable_independent", content_hash="same"),
        _source("b", level="reliable_independent", content_hash="same"),
    ])
    assert independent.status == "confirmed"
    assert same_domain.status == "insufficient"
    assert same_body.status == "insufficient"


def test_reliable_source_conflict_is_never_guessed() -> None:
    result = assess_claim([
        _source("a", level="official_primary"),
        _source("b", level="reliable_independent", relation="contradicts"),
    ])
    assert result.status == "conflict"
    assert result.supporting_source_ids == ("a",)
    assert result.contradicting_source_ids == ("b",)


def test_relaxed_gate_reliable_plus_corroborator_confirms() -> None:
    """2026-09-05 放宽：可靠来源 + 任意级别独立交叉佐证 → confirmed。"""
    result = assess_claim([
        _source("gov-1", level="reliable_independent"),
        _source("blog-1", level="general"),
    ])
    assert result.status == "confirmed"


def test_relaxed_gate_still_needs_independent_family() -> None:
    """放宽后仍要求同域/同文不算独立佐证；仅单 general 仍 insufficient。"""
    same = assess_claim([
        _source("gov-1", level="reliable_independent", domain="example.gov.cn"),
        _source("gov-2", level="general", domain="example.gov.cn"),  # 同 registered domain
    ])
    assert same.status == "insufficient"
    lone = assess_claim([_source("blog-1", level="general")])
    assert lone.status == "insufficient"
