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
from xiaowo_web.evidence.rewrite import (
    WECHAT_TRIGGER_RE,
    QueryRewriter,
    official_site_query,
    temporal_anchor,
    wechat_query,
)
from xiaowo_web.evidence.wechat import WechatClient
from xiaowo_web.evidence.trust import SourceTrustStore, registered_domain
from xiaowo_web.evidence.url_security import UrlGuard, UrlSafetyError
from xiaowo_web.settings import WebSettings


StageCallback = Callable[[str, str], None]


# 引用匹配归一化：去除空白与常见中英文标点，全角转半角，降低大小写。
# LLM 抽取的 quote 常有标点/空格层面的改写，逐字匹配会导致大量可用证据被丢弃。
_MATCH_NOISE_RE = re.compile(r"[\s，。；：、！？「」『』（）《》〈〉【】“”‘’…·,.;:!?()\[\]{}<>\"'—\-]+")
_FULLWIDTH = str.maketrans("＂＂＇＇，。；：！？（）【】", '""\'\',.;:!?()[]')


def _match_text(value: str) -> str:
    normalized = value.translate(_FULLWIDTH).lower()
    return _MATCH_NOISE_RE.sub("", normalized)


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

    async def answer(
        self,
        question: str,
        *,
        profile: dict | None = None,
        on_stage: StageCallback | None = None,
        rounds_limit: int | None = None,
    ) -> AnswerBundle:
        try:
            sanitized = sanitize_public_query(question, profile)
        except QuerySafetyError as exc:
            return self._insufficient([], [exc.message], terminal_reason=exc.code)

        sources_acc: list[dict] = []
        wechat_sources: list[dict] = []  # 2026-09-05：公众号未确认时保留，供互联网检索后合并
        limitations_acc: list[str] = []
        claims_acc: list[dict] | None = None
        year_anchor = temporal_anchor(sanitized.text) if self.settings.web_query_rewrite else None

        # 公众号优先分支：科大相关问题先检索微信公众号（信息密度高；置信裁决不变）
        if (
            self.wechat is not None
            and self.settings.wechat_enabled
            and WECHAT_TRIGGER_RE.search(sanitized.text)
        ):
            self._stage(on_stage, "web_search", "正在检索微信公众号")
            try:
                bundle = await asyncio.wait_for(
                    # 微信查询改写：官方名称词+业务词（原文长句在搜狗微信索引匹配差 → 噪音/漏命中）
                    self.wechat.collect(wechat_query(sanitized.text)),
                    timeout=max(15.0, min(25.0, self.settings.run_timeout_seconds * 0.4)),
                )
            except asyncio.TimeoutError:
                bundle = None
                limitations_acc.append("微信公众号检索超时，已回退通用检索。")
            if bundle is not None and bundle.articles:
                pages = await self._wechat_pages(bundle.articles)
                # 2026-09-05 相关性过滤：非官方号文章标题必须命中查询核心词（去官方名），
                # 否则丢弃（搜狗对"中科大 x"类常返回标题含"中科大"但内容无关的公众号）
                if pages:
                    business_words = self._wechat_core_words(sanitized.text)
                    if business_words:
                        kept = [
                            p for p in pages
                            if p.trust.rule_id == "wechat_official"
                            or any(w in str(p.page.title or "") for w in business_words)
                        ]
                        dropped = len(pages) - len(kept)
                        pages = kept
                        if dropped and dropped > 0:
                            limitations_acc.append(f"微信公众号命中 {dropped} 条与问题无关的内容，已忽略。")
                if pages:
                    confirmed, claims = await self._assess_and_answer(
                        pages, sanitized.text, limitations_acc, year_anchor, on_stage,
                    )
                    if confirmed is not None:
                        return confirmed
                    # 2026-09-05 体验放宽：公众号已查看但未达门槛 → 保留微信来源，继续通用互联网搜索
                    wechat_sources = [
                        self._public_source(record, index + 1) for index, record in enumerate(pages)
                    ]
                    limitations_acc.append("已查看公众号内容，但尚无声明达到确定性证据门槛；继续检索互联网公开页面。")

        queries = await self._candidate_queries(sanitized.text)
        # 2026-09-04 提速：auto 模式本地有兜底 → 单轮（无本地兜底的 web 模式保持 settings 轮数）
        max_rounds = max(1, min(rounds_limit or self.settings.web_search_max_rounds,
                                 self.settings.web_search_max_rounds))

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
            ranked = await self._rerank_hits(query, ranked)
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
            confirmed, claims = await self._assess_and_answer(
                pages, sanitized.text, limitations_acc, year_anchor, on_stage,
            )
            # insufficient 收束只保留最后一轮 sources（与 claims 同源）：
            # 跨轮累计会导致 citation 每轮从 1 重复编号，声明与来源对不上号
            sources_acc = sources_accext
            if confirmed is not None:
                return confirmed
            claims_acc = claims

        merged_sources = list(sources_acc)
        seen_sids = {str(item.get("source_id") or "") for item in merged_sources}
        for item in wechat_sources:
            if str(item.get("source_id") or "") not in seen_sids:
                merged_sources.append(item)
        return self._insufficient(merged_sources, limitations_acc, claims=claims_acc)


    async def _assess_and_answer(
        self,
        pages: list[_PageRecord],
        question: str,
        limitations: list[str],
        year_anchor: str | None,
        on_stage: StageCallback | None,
    ) -> tuple[AnswerBundle | None, list[dict] | None]:
        """抽取 + 置信裁决 + 组装回答；确认/分歧返回 Bundle，不够则追加 limitation 返回 None。

        返回 (bundle_or_none, claims)：claims 按调用返回（局部变量），
        不再落实例属性——EvidencePipeline 是全局单例，实例属性会在并发请求间串改。"""
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
        if not confirmed_lines and not conflict_lines:
            limitations.append("已找到公开来源，但尚无声明达到确定性证据门槛。")
            return None, claims
        if not confirmed_lines:
            limitations.append("已找到来源但声明存在分歧，继续核验。")
            return None, claims
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
        ), claims

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
            return None, ["联网搜索当前不可用或超时。"]
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
                return None, ["联网搜索当前不可用或超时。"]
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
        normalized_pages = {
            source_id: _match_text(record.page.markdown)
            for source_id, record in pages.items()
        }
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
                if record is None or len(quote) < 12:
                    continue
                if _match_text(quote) not in normalized_pages[evidence.source_id]:
                    continue
                if year_anchor:
                    published_year = self._published_year(record.page)
                    if published_year and not self._evidence_year_ok(record.page, year_anchor):
                        seen_mismatch = True
                        continue
                    if published_year is None:
                        seen_undated = True
                excerpt_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
                near_hash = hashlib.sha256(" ".join(record.page.markdown.split())[:4000].encode("utf-8")).hexdigest()
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

    @staticmethod
    def _wechat_core_words(question: str) -> list[str]:
        """微信命中相关性词：去官方名后的中文 2 字窗口（含"月饼"类词）。
        疑问词停用（怎么/什么…）避免宽度匹配；词表外查询也能获得业务词。"""
        text = question or ""
        for name in ("中国科学技术大学", "中科大", "中国科大", "USTC", "科大"):
            text = text.replace(name, "")
        import re as _re
        stop = ("怎么", "什么", "多少", "何时", "哪里", "如何", "啥", "吗", "呢", "哪个", "多久")
        words: list[str] = []
        for seg in _re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for i in range(len(seg) - 1):
                w = seg[i:i + 2]
                if w not in stop:
                    words.append(w)
        return list(dict.fromkeys(words))

    def _rank_hit(self, hit: SearchHit) -> tuple[int, str]:
        decision = self.trust_store.classify_url_without_dns(hit.url)
        rank = {"official_primary": 0, "reliable_independent": 1, "general": 2, "unverified": 3}
        # 2026-09-05：信任级同级内，权威分高者优先（百度 authority_score：1 权威 / 0.5 普通）
        authority = f"{float(getattr(hit, 'rank_hint', 0.5)):0.1f}"
        return rank.get(decision.level, 3), authority, hit.url


    async def _rerank_hits(self, query: str, ranked: list[SearchHit]) -> list[SearchHit]:
        """搜索命中语义精排：信任级为主序、bge-reranker 相关分为副序（本地 ONNX）。

        搜索引擎 top-N 在信任级内常按任意顺序返回；抓取预算只有 3 条，
        语义精排让"与问题最相关"的页面优先被抓取。模型缺失/失败时回退原序。
        """
        if len(ranked) < 3:
            return ranked
        try:
            from knowledge.reranker import rerank

            docs = [f"{hit.title}\n{hit.snippet}" for hit in ranked]
            order = await asyncio.to_thread(rerank, query, docs, len(docs))
        except Exception:
            return ranked
        if not order or len(order) != len(ranked):
            return ranked
        pos_of = {index: position for position, index in enumerate(order)}
        trust_keys = [self._rank_hit(hit)[0] for hit in ranked]
        ordered = sorted(range(len(ranked)), key=lambda i: (trust_keys[i], pos_of[i]))
        return [ranked[i] for i in ordered]

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
        # 2026-09-04 证据制软化：提取到声明但未达确认门槛时，仍展示内容+免责，
        # 而不是只有兜底文案；无内容才用固定文案。
        placeholder = "暂未找到足够可靠的联网证据。"
        real_claims = [
            dict(item) for item in (claims or [])
            if str(item.get("text") or "").strip()
            and not str(item.get("text") or "").strip().startswith("暂未找到")
        ]
        if real_claims:
            body = "\n".join(
                f"- {str(item.get('text') or '').strip()}" for item in real_claims[:5]
            )
            notes = [str(item).strip() for item in limitations if str(item).strip()]
            markdown = (
                "已找到以下相关信息（来源未达确定性门槛，仅供参考）：\n\n"
                f"{body}\n\n"
                f"{'提示：' + '；'.join(notes[:2]) if notes else ''}"
            )
        elif sources:
            # 2026-09-05 体验放宽：有来源但提取/裁决不足 → 列出检索到的相关内容（附来源编号）
            lines = []
            for index, item in enumerate(sources[:5], start=1):
                title = str(item.get("title") or item.get("institution") or "").strip()
                if title:
                    lines.append(f"- 《{title}》[{index}]")
            markdown = (
                "已检索到以下相关内容（尚未达到确定性验证门槛，仅供参考）：\n\n"
                + "\n".join(lines)
                + "\n\n如需核实，可点击上方来源链接。"
            )
            real_claims = [{
                "claim_id": "c1", "text": markdown, "kind": "factual",
                "status": "insufficient", "evidence": [],
            }]
        else:
            markdown = placeholder
            real_claims = [{
                "claim_id": "c1", "text": placeholder, "kind": "factual",
                "status": "insufficient", "evidence": [],
            }]
        return AnswerBundle(
            markdown=markdown,
            claims=real_claims,
            sources=sources,
            limitations=limitations,
            terminal_reason=terminal_reason,
        )

    async def close(self) -> None:
        await self.search.close()
        await self.crawler.close()
        if self.wechat is not None:
            await self.wechat.close()
