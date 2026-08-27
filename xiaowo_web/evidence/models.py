"""Typed contracts shared by search, crawling, trust, and claim gating."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SourceLevel = Literal["official_primary", "reliable_independent", "general", "unverified"]
EvidenceRelation = Literal["supports", "contradicts", "context"]


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    normalized_url: str
    scheme: str
    host: str
    port: int
    path: str
    approved_ips: tuple[str, ...]
    ustc_domain: bool


@dataclass(frozen=True, slots=True)
class TrustDecision:
    level: SourceLevel
    institution: str
    tags: tuple[str, ...] = ()
    rule_id: str | None = None


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class SearchBatch:
    hits: list[SearchHit]
    partial: bool = False
    unavailable_engines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CrawledPage:
    requested_url: str
    final_url: str
    title: str
    markdown: str
    status_code: int
    content_type: str
    fetched_at: str
    published_at: str | None
    content_hash: str
    robots_allowed: bool
    peer_ip_verified: bool


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    source_id: str
    normalized_url: str
    registered_domain: str
    level: SourceLevel
    relation: EvidenceRelation
    content_hash: str
    near_duplicate_hash: str
    upstream_url: str | None = None
    usable: bool = True
    expired: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClaimAssessment:
    status: Literal["confirmed", "conflict", "insufficient"]
    supporting_source_ids: tuple[str, ...]
    contradicting_source_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ExtractedEvidence:
    source_id: str
    relation: EvidenceRelation
    quote: str
    upstream_url: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    text: str
    evidence: tuple[ExtractedEvidence, ...]
