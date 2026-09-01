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
from xiaowo_web.evidence.rewrite import QueryRewriter, official_site_query, temporal_anchor
from xiaowo_web.evidence.wechat import WechatClient
from xiaowo_web.evidence.trust import SourceTrustStore, registered_domain
from xiaowo_web.evidence.url_security import UrlGuard, UrlSafetyError
from xiaowo_web.settings import WebSettings


StageCallback = Callable[[str, str], None]

# 公众号优先触发词（2026-09-01 用户确定）
_WECHAT_TRIGGER_RE = re.compile(r"科大|中科大|USTC|中国科学技术大学")


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
        wechat: WechatClient | None = None,
    ) -> None:
        self.settings = settings
        self.search = search
        self.crawler = crawler
        self.url_guard = url_guard or UrlGuard()
        self.trust_store = trust_store or SourceTrustStore()
        self.extractor = extractor
        self.rewriter = rewriter or QueryRewriter()
        self.wechat = wechat
        self._last_claims: list[dict] | None = None

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

        sources_acc: list[dict] = []
        limitations_acc: list[str] = []
        claims_acc: list[dict] | None = None
        year_anchor = temporal_anchor(sanitized.text) if self.settings.web_query_rewrite else None

        # 公众号优先分支：科大相关问题先检索微信公众号（信息密度高；置信裁决不变）
        if (
            self.wechat is not None
            and self.settings.wechat_enabled
            and _WECHAT_TRIGGER_RE.search(sanitized.text)
        ):
            self._stage(on_stage, "web_search", "正在检索微信公众号")
            try:
                bundle = await asyncio.wait_for(
                    self.wechat.collect(sanitized.text),
                    timeout=max(15.0, min(35.0, self.settings.run_timeout_seconds * 0.5)),
                )
            except asyncio.TimeoutError:
                bundle = None
                limitations_acc.append("微信公众号检索超时，已回退通用检索。")
            if bundle is not None and bundle.articles:
                pages = await self._wechat_pages(bundle.articles)
                if pages:
                    confirmed = await self._assess_and_answer(
                        pages, sanitized.text, limitations_acc, year_anchor, on_stage,
                    )
                    if confirmed is not None:
                        return confirmed
                    # 公众号内容已查看但不达门槛：以公众号来源收束（不再叠加通用两轮，守住总预算）
                    sources = [self._public_source(record, index + 1) for index, record in enumerate(pages)]
                    limitations_acc.append("已查看公众号内容，但尚无声明达到确定性证据门槛。")
                    return self._insufficient(sources, limitations_acc, claims=self._last_claims)

        queries = await self._candidate_queries(sanitized.text)
        max_rounds = max(1, self.settings.web_search_max_rounds)

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

            sources_accext = [self._public_source(record, index + 1) for index, record in enumerate(pages)]
            confirmed = await self._assess_and_answer(
                pages, sanitized.text, limitations_acc, year_anchor, on_stage,
            )
            sources_acc.extend(sources_accext)
            if confirmed is not None:
                return confirmed
            claims_acc = getattr(self, "_last_claims", claims_acc)

        return self._insufficient(sources_acc, limitations_acc, claims=claims_acc)


    async def _assess_and_answer(
        self,
        pages: list[_PageRecord],
        question: str,
        limitations: list[str],
        year_anchor: str | None,
        on_stage: StageCallback | None,
    ) -> AnswerBundle | None:
        """抽取 + 置信裁决 + 组装回答；确认/分歧返回 Bundle，不够则追加 limitation 返回 None。"""
        self._stage(on_stage, "evidence_check", "正在核验证据")
        try:
            extracted = await self.extractor.extract(
                question, [(record.source_id, record.page) for record in pages],
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            extracted = []
            limitations.append("结构化证据提取暂不可用，未输出未经核验的结论。")
        extractor_code = getattr(self.extractor, "last_error_code", None)
        if extractor_code and not any("结构化证据提取" in item for item in limitations):
            messages = {
                "EXTRACTOR_NOT_CONFIGURED": "结构化证据提取未配置经过验证的模型。",
                "EXTRACTOR_INVALID_RESPONSE": "结构化证据模型返回格式未通过校验。",
                "EXTRACTOR_CALL_FAILED": "结构化证据模型调用失败。",
                "EXTRACTOR_PROBE_FAILED": "结构化证据模型能力探针失败。",
            }
            limitations.append(messages.get(extractor_code, "结构化证据提取暂不可用。"))
        claims, confirmed_lines, conflict_lines, ingestion_spans, year_notes = self._verify_claims(
            extracted, pages, year_anchor=year_anchor,
        )
        if year_notes:
            limitations.extend(item for item in year_notes if item not in limitations)
        self._last_claims = claims
        if not confirmed_lines and not conflict_lines:
            limitations.append("已找到公开来源，但尚无声明达到确定性证据门槛。")
            return None
        if not confirmed_lines:
            limitations.append("已找到来源但声明存在分歧，继续核验。")
            return None
        sources = [self._public_source(record, index + 1) for index, record in enumerate(pages)]
        sections: list[str] = [s for s in confirmed_lines if s]
        if conflict_lines:
            sections.append("信息存在分歧\n\n" + "\n".join(conflict_lines))
        return AnswerBundle(
            markdown="\n\n".join(sections),
            claims=claims,
            sources=sources,
            limitations=limitations,
            terminal_reason="web_evidence_confirmed",
            ingestion_candidates=self._ingestion_candidates(pages, ingestion_spans),
        )

    async def _wechat_pages(self, articles) -> list[_PageRecord]:
        from datetime import UTC, datetime

        from xiaowo_web.evidence.wechat import (
            article_content_hash,
            build_markdown,
            is_official_account,
        )

        pages: list[_PageRecord] = []
        for article in articles:
            if not article.markdown and not article.ocr_spans:
                continue
            try:
                validated = await asyncio.to_thread(self.url_guard.validate, article.url)
            except UrlSafetyError:
                continue
            official = is_official_account(article.author)
            page = CrawledPage(
                requested_url=article.url,
                final_url=article.url,
                title=article.title,
                markdown=build_markdown(article.markdown, article.ocr_spans),
                status_code=200,
                content_type="text/html",
                fetched_at=datetime.now(UTC).isoformat(),
                published_at=article.published_at,
                content_hash=article_content_hash(article),
                robots_allowed=True,
                peer_ip_verified=True,
            )
            source_id = "s-" + hashlib.sha256(article.url.encode("utf-8")).hexdigest()[:12]
            pages.append(_PageRecord(
                source_id=source_id,
                url=validated,
                trust=TrustDecision(
                    level="official_primary" if official else "unverified",
                    institution=article.author or "微信公众号",
                    tags=("wechat",),
                    rule_id="wechat_official" if official else None,
                ),
                page=page,
                citation=0,
            ))
        return pages

    async def _candidate_queries(self, question: str) -> list[str]:
        """Rewrite long questions into 1-2 keyword queries; original on any failure.

        校内事务问题额外注入 site 官方站点查询（第二位优先，提高权威一手来源命中率）。
        """
        if self.settings.web_query_rewrite:
            rewritten = await self.rewriter.rewrite(question)
        else:
            rewritten = None
        queries = rewritten or [question]
        site_query = official_site_query(question)
        if site_query and site_query not in queries:
            queries.insert(1, site_query)
        return queries[:3]

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
        *,
        year_anchor: str | None = None,
    ) -> tuple[list[dict], list[str], list[str], dict[str, list[str]], list[str]]:
        pages = {record.source_id: record for record in page_records}
        citation_map = {record.source_id: index + 1 for index, record in enumerate(page_records)}
        claims: list[dict] = []
        confirmed_lines: list[str] = []
        conflict_lines: list[str] = []
        ingestion_spans: dict[str, list[str]] = {}
        year_notes: list[str] = []
        seen_mismatch = False
        seen_undated = False
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
                if year_anchor:
                    published_year = self._published_year(record.page)
                    if published_year and not self._evidence_year_ok(record.page, year_anchor):
                        seen_mismatch = True
                        continue
                    if published_year is None:
                        seen_undated = True
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
        if seen_mismatch:
            year_notes.append("已排除发布年份与问题年份不一致的联网证据。")
        if seen_undated:
            year_notes.append("部分来源未标注发布时间，年份一致性未核对。")
        return claims, confirmed_lines, conflict_lines, ingestion_spans, year_notes

    @staticmethod
    def _published_year(page: CrawledPage) -> str | None:
        published = (page.published_at or "").strip()
        match = re.search(r"(20\d{2})", published)
        return match.group(1) if match else None

    @staticmethod
    def _evidence_year_ok(page: CrawledPage, year_anchor: str) -> bool:
        """年份引用侧核对：问题含年份锚时，证据的发布年份必须与锚一致。"""
        published_year = EvidencePipeline._published_year(page)
        return published_year is None or published_year == year_anchor

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
        if self.wechat is not None:
            await self.wechat.close()
