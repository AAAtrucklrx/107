"""Deterministic one-official-or-two-independent evidence gate."""

from __future__ import annotations

from xiaowo_web.evidence.models import ClaimAssessment, EvidenceSource


_RELIABLE_LEVELS = frozenset({"official_primary", "reliable_independent"})


def _same_family(left: EvidenceSource, right: EvidenceSource) -> bool:
    if left.normalized_url == right.normalized_url:
        return True
    if left.registered_domain == right.registered_domain:
        return True
    if left.content_hash and left.content_hash == right.content_hash:
        return True
    if left.near_duplicate_hash and left.near_duplicate_hash == right.near_duplicate_hash:
        return True
    if left.upstream_url and right.upstream_url and left.upstream_url == right.upstream_url:
        return True
    return False


def independent_families(sources: list[EvidenceSource]) -> list[list[EvidenceSource]]:
    families: list[list[EvidenceSource]] = []
    for source in sources:
        matching = [family for family in families if any(_same_family(source, item) for item in family)]
        if not matching:
            families.append([source])
            continue
        merged = [source]
        for family in matching:
            merged.extend(family)
            families.remove(family)
        families.append(merged)
    return families


def assess_claim(sources: list[EvidenceSource]) -> ClaimAssessment:
    usable = [source for source in sources if source.usable and not source.expired]
    supports = [source for source in usable if source.relation == "supports"]
    contradicts = [source for source in usable if source.relation == "contradicts"]
    reliable_supports = [source for source in supports if source.level in _RELIABLE_LEVELS]
    reliable_contradicts = [source for source in contradicts if source.level in _RELIABLE_LEVELS]

    if reliable_supports and reliable_contradicts:
        return ClaimAssessment(
            status="conflict",
            supporting_source_ids=tuple(source.source_id for source in reliable_supports),
            contradicting_source_ids=tuple(source.source_id for source in reliable_contradicts),
            reason="可靠来源对该声明存在实质分歧。",
        )
    official = [source for source in reliable_supports if source.level == "official_primary"]
    if official:
        return ClaimAssessment(
            status="confirmed",
            supporting_source_ids=(official[0].source_id,),
            contradicting_source_ids=(),
            reason="一个审核白名单中的直接权威一手来源支持该声明。",
        )
    families = independent_families(reliable_supports)
    if len(families) >= 2:
        return ClaimAssessment(
            status="confirmed",
            supporting_source_ids=(families[0][0].source_id, families[1][0].source_id),
            contradicting_source_ids=(),
            reason="两个相互独立且一致的可靠来源支持该声明。",
        )
    # 2026-09-05 门槛放宽：1 个可靠来源 + 1 个任意级别的独立交叉佐证 → 确认
    # （主证据可靠优先；普通来源仅作佐证，仍需独立家族）
    if reliable_supports:
        corroborators = [
            source for source in supports
            if source.level not in _RELIABLE_LEVELS
            and not any(_same_family(source, r) for r in reliable_supports)
        ]
        if corroborators:
            return ClaimAssessment(
                status="confirmed",
                supporting_source_ids=(reliable_supports[0].source_id, corroborators[0].source_id),
                contradicting_source_ids=(),
                reason="一个可靠来源支持并有独立普通来源交叉佐证。",
            )
    return ClaimAssessment(
        status="insufficient",
        supporting_source_ids=tuple(source.source_id for source in supports),
        contradicting_source_ids=tuple(source.source_id for source in contradicts),
        reason="暂未达到一个权威一手来源或两个独立可靠来源的门槛。",
    )
