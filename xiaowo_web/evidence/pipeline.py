"""Bounded search/crawl pipeline that only emits claims passing deterministic gates."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from xiaowo_web.chat.models import AnswerBundle
from xiaowo_web.evidence.clients import Crawl4AiClient, SearxngClient, SidecarContractError
from xiaowo_web.evidence.gate import assess_claim
from xiaowo_web.evidence.models import (
    CrawledPage,
    EvidenceSource,
    ExtractedClaim,
    SearchHit,
    TrustDecision,
    ValidatedUrl,
)
from xiaowo_web.evidence.privacy import QuerySafetyError, sanitize_public_query
from xiaowo_web.evidence.rewrite import QueryRewriter
from xiaowo_web.evidence.trust import SourceTrustStore, registered_domain
from xiaowo_web.evidence.url_security import UrlGuard, UrlSafetyError
from xiaowo_web.settings import WebSettings


StageCallback = Callable[[str, str], None]


class ClaimExtractor(Protocol):
    async def extract(
        self,
        question: str,
        pages: list[tuple[str, CrawledPage]],
    ) -> list[ExtractedClaim]: ...


@dataclass(frozen=True, slots=True)
class _PageRecord:
    source_id: str
    url: ValidatedUrl
    trust: TrustDecision
    page: CrawledPage
    citation: int


class EvidencePipeline:
    def __init__(
        self,
        settings: WebSettings,
        search: SearxngClient,
        crawler: Crawl4AiClient,
        *,
        url_guard: UrlGuard | None = None,
        trust_store: SourceTrustStore | None = None,
        extractor: ClaimExtractor,
        rewriter: QueryRewriter | None = None,
    ) -> None:
        self.settings = settings
        self.search = search
        self.crawler = crawler
        self.url_guard = url_guard or UrlGuard()
        self.trust_store = trust_store or SourceTrustStore()
        self.extractor = extractor
        self.rewriter = rewriter or QueryRewriter()

    async def answer(
        self,
        question: str,
        *,
        profile: dict | None = None,
        on_stage: StageCallback | None = None,
    ) -> AnswerBundle:
        try:
            sanitized = sanitize_public_query(question, profile)
        except QuerySafetyError as exc:
            return self._insufficient([], [exc.message], terminal_reason=exc.code)

        queries = await self._candidate_queries(sanitized.text)
        max_rounds = max(1, self.settings.web_search_max_rounds)
        sources_acc: list[dict] = []
        limitations_acc: list[str] = []
        claims_acc: list[dict] | None = None

        for round_index in range(max_rounds):
            if round_index >= len(queries):
                if (
                    round_index == 1
                    and len(queries) == 1
                    and self.settings.web_query_rewrite
                ):
                    # 唯一候选失败且还有加轮预算：请改写器给出更简短的另一组关键词。
                    extra = await self.rewriter.rewrite(sanitized.text, short_hint=True)
                    if extra:
                        queries.extend(extra)
                if round_index >= len(queries):
                    break
            query = queries[round_index]
            if round_index > 0 and query == queries[round_index - 1]:
                break

            self._stage(on_stage, "web_search", "正在联网搜索")
            batch, search_limitations = await self._search_once(query)
            limitations_acc.extend(search_limitations)
            if batch is None:
                continue
            if not batch.hits:
                limitations_acc.append(f"第 {round_index + 1} 轮检索未命中，尝试其他关键词。")
                continue

            ranked = sorted(batch.hits, key=self._rank_hit)
            validated_hits: list[tuple[SearchHit, ValidatedUrl, TrustDecision]] = []
            for hit in ranked:
                try:
                    validated = await asyncio.to_thread(self.url_guard.validate, hit.url)
                except UrlSafetyError:
                    continue
                trust = self.trust_store.classify(validated)
                validated_hits.append((hit, validated, trust))
                if len(validated_hits) >= 3:
                    break
            if not validated_hits:
                limitations_acc.append("无搜索结果通过公开网络地址与来源安全校验。")
                continue

            self._stage(on_stage, "web_fetch", "正在抓取公开页面")
            if not await self.crawler.health():
                return self._insufficient(
                    [], limitations_acc + ["Crawl4AI 未通过 egress、robots 与连接固定健康检查。"],
                    terminal_reason="CRAWL_BLOCKED",
                )
            tasks = [self._crawl_one(hit, validated, trust) for hit, validated, trust in validated_hits]
            try:
                crawled = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=max(0.1, self.settings.evidence_timeout_seconds - self.settings.search_timeout_seconds),
                )
            except TimeoutError:
                crawled = []
                limitations_acc.append("网页抓取达到证据预算。")
            pages = [item for item in crawled if isinstance(item, _PageRecord)]
            if len(pages) < len(validated_hits):
                limitations_acc.append("部分候选页面未通过抓取或重定向后的安全校验。")
            if not pages:
                limitations_acc.append("本轮未抓到可用页面。")
                continue

            self._stage(on_stage, "evidence_check", "正在核验证据")
            try:
                extracted = await self.extractor.extract(
                    sanitized.text,
                    [(record.source_id, record.page) for record in pages],
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                extracted = []
                limitations_acc.append("结构化证据提取暂不可用，未输出未经核验的结论。")
            extractor_code = getattr(self.extractor, "last_error_code", None)
            if extractor_code and not any("结构化证据提取" in item for item in limitations_acc):
                messages = {
                    "EXTRACTOR_NOT_CONFIGURED": "结构化证据提取未配置经过验证的模型。",
                    "EXTRACTOR_INVALID_RESPONSE": "结构化证据模型返回格式未通过校验。",
                    "EXTRACTOR_CALL_FAILED": "结构化证据模型调用失败。",
                    "EXTRACTOR_PROBE_FAILED": "结构化证据模型能力探针失败。",
                }
                limitations_acc.append(messages.get(extractor_code, "结构化证据提取暂不可用。"))
            claims, confirmed_lines, conflict_lines, ingestion_spans = self._verify_claims(extracted, pages)
            claims_acc = claims
            sources_accext = [self._public_source(record, index + 1) for index, record in enumerate(pages)]
            if confirmed_lines or conflict_lines:
                if confirmed_lines:
                    sections: list[str] = [s for s in confirmed_lines if s]
                    if conflict_lines:
                        sections.append("信息存在分歧\n\n" + "\n".join(conflict_lines))
                    return AnswerBundle(
                        markdown="\n\n".join(sections),
                        claims=claims,
                        sources=sources_accext,
                        limitations=limitations_acc,
                        terminal_reason="web_evidence_confirmed",
                        ingestion_candidates=self._ingestion_candidates(pages, ingestion_spans),
                    )
                # 只有冲突、无确认：视为证据不足，继续加轮（下次若确认则确认，否则最终如实列出）
                limitations_acc.append("已找到来源但声明存在分歧，继续核验。")
                sources_acc.extend(sources_accext)
                continue

            sources_acc.extend(sources_accext)
            limitations_acc.append("已找到公开来源，但尚无声明达到确定性证据门槛。")

        return self._insufficient(sources_acc, limitations_acc, claims=claims_acc)

    async def _candidate_queries(self, question: str) -> list[str]:
        """Rewrite long questions into 1-2 keyword queries; original on any failure."""
        if not self.settings.web_query_rewrite:
            return [question]
        rewritten = await self.rewriter.rewrite(question)
        return rewritten or [question]

    async def _search_once(self, query: str) -> tuple[SearchBatch | None, list[str]]:
        """One search attempt with a single empty-result retry (engine rate limits)."""
        try:
            batch = await asyncio.wait_for(
                self.search.search(query, limit=10),
                timeout=self.settings.search_timeout_seconds,
            )
        except (TimeoutError, SidecarContractError, Exception) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None, ["SearXNG 搜索当前不可用或超时。"]
        if not batch.hits:
            await asyncio.sleep(1.5)
            try:
                batch = await asyncio.wait_for(
                    self.search.search(query, limit=10),
                    timeout=self.settings.search_timeout_seconds,
                )
            except (TimeoutError, SidecarContractError, Exception) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return None, ["SearXNG 搜索当前不可用或超时。"]
        limitations = ["部分搜索引擎未响应，结果可能不完整。"] if batch.partial else []
        return batch, limitations

    async def _crawl_one(
        self,
        _hit: SearchHit,
        validated: ValidatedUrl,
        _trust: TrustDecision,
    ) -> _PageRecord:
        page = await self.crawler.crawl(validated.normalized_url)
        final = await asyncio.to_thread(self.url_guard.validate, page.final_url)
        trust = self.trust_store.classify(final)
        source_id = "s-" + hashlib.sha256(final.normalized_url.encode("utf-8")).hexdigest()[:12]
        return _PageRecord(source_id=source_id, url=final, trust=trust, page=page, citation=0)

    def _verify_claims(
        self,
        extracted: list[ExtractedClaim],
        page_records: list[_PageRecord],
    ) -> tuple[list[dict], list[str], list[str], dict[str, list[str]]]:
        pages = {record.source_id: record for record in page_records}
        citation_map = {record.source_id: index + 1 for index, record in enumerate(page_records)}
        claims: list[dict] = []
        confirmed_lines: list[str] = []
        conflict_lines: list[str] = []
        ingestion_spans: dict[str, list[str]] = {}
        for index, candidate in enumerate(extracted[:20], start=1):
            if not candidate.text.strip():
                continue
            evidence_sources: list[EvidenceSource] = []
            public_evidence: list[dict] = []
            for evidence in candidate.evidence:
                record = pages.get(evidence.source_id)
                quote = " ".join(evidence.quote.split()).strip()
                page_text = " ".join(record.page.markdown.split()) if record else ""
                if record is None or len(quote) < 12 or quote not in page_text:
                    continue
                excerpt_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
                near_hash = hashlib.sha256(page_text[:4000].encode("utf-8")).hexdigest()
                evidence_sources.append(EvidenceSource(
                    source_id=record.source_id,
                    normalized_url=record.url.normalized_url,
                    registered_domain=registered_domain(record.url.host),
                    level=record.trust.level,
                    relation=evidence.relation,
                    content_hash=record.page.content_hash,
                    near_duplicate_hash=near_hash,
                    upstream_url=None,
                    usable=True,
                    expired=False,
                ))
                public_evidence.append({
                    "source_id": record.source_id,
                    "evidence_type": "web",
                    "relation": evidence.relation,
                    "excerpt_hash": excerpt_hash,
                    "citation": citation_map[record.source_id],
                })
            assessment = assess_claim(evidence_sources)
            claim_id = f"c{index}"
            claims.append({
                "claim_id": claim_id,
                "text": candidate.text.strip(),
                "kind": "factual",
                "status": assessment.status,
                "evidence": public_evidence,
            })
            citations = sorted({
                citation_map[source_id]
                for source_id in (
                    *assessment.supporting_source_ids,
                    *assessment.contradicting_source_ids,
                )
                if source_id in citation_map
            })
            suffix = "".join(f"[{citation}]" for citation in citations)
            if assessment.status == "confirmed":
                confirmed_lines.append(f"{candidate.text.strip()} {suffix}".strip())
                accepted_ids = set(assessment.supporting_source_ids)
                for item in public_evidence:
                    if item["source_id"] in accepted_ids and item["relation"] == "supports":
                        ingestion_spans.setdefault(item["source_id"], []).append(item["excerpt_hash"])
            elif assessment.status == "conflict":
                conflict_lines.append(f"- {candidate.text.strip()} {suffix}".strip())
        return claims, confirmed_lines, conflict_lines, ingestion_spans

    @staticmethod
    def _ingestion_candidates(
        pages: list[_PageRecord],
        ingestion_spans: dict[str, list[str]],
    ) -> list[dict]:
        candidates: list[dict] = []
        for record in pages:
            spans = sorted(set(ingestion_spans.get(record.source_id) or []))
            if not spans or record.trust.level == "unverified":
                continue
            candidates.append({
                "source_id": record.source_id,
                "normalized_url": record.url.normalized_url,
                "final_url": record.url.normalized_url,
                "title": record.page.title,
                "institution": record.trust.institution,
                "level": record.trust.level,
                "fetched_at": record.page.fetched_at,
                "content_type": record.page.content_type,
                "snapshot_text": record.page.markdown,
                "evidence_span_hash": hashlib.sha256("|".join(spans).encode("utf-8")).hexdigest(),
            })
        return candidates

    def _rank_hit(self, hit: SearchHit) -> tuple[int, str]:
        decision = self.trust_store.classify_url_without_dns(hit.url)
        rank = {"official_primary": 0, "reliable_independent": 1, "general": 2, "unverified": 3}
        return rank.get(decision.level, 3), hit.url

    @staticmethod
    def _public_source(record: _PageRecord, citation: int) -> dict:
        return {
            "source_id": record.source_id,
            "title": record.page.title or record.url.host,
            "display_url": record.url.normalized_url,
            "institution": record.trust.institution,
            "domain": record.url.host,
            "published_at": record.page.published_at,
            "fetched_at": record.page.fetched_at,
            "level": record.trust.level,
            "validity": "active",
            "citation": citation,
            "tags": list(record.trust.tags),
        }

    @staticmethod
    def _stage(callback: StageCallback | None, stage: str, message: str) -> None:
        if callback is not None:
            callback(stage, message)

    @staticmethod
    def _insufficient(
        sources: list[dict],
        limitations: list[str],
        *,
        terminal_reason: str = "EVIDENCE_INSUFFICIENT",
        claims: list[dict] | None = None,
    ) -> AnswerBundle:
        return AnswerBundle(
            markdown="暂未找到足够可靠的联网证据。",
            claims=claims or [{
                "claim_id": "c1",
                "text": "暂未找到足够可靠的联网证据。",
                "kind": "factual",
                "status": "insufficient",
                "evidence": [],
            }],
            sources=sources,
            limitations=limitations,
            terminal_reason=terminal_reason,
        )

    async def close(self) -> None:
        await self.search.close()
        await self.crawler.close()
