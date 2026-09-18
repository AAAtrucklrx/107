"""Bounded search/crawl pipeline that only emits claims passing deterministic gates."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
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
from xiaowo_web.evidence.compose import compose_and_verify, judge_relevance
from xiaowo_web.evidence.rewrite import (
    WECHAT_TRIGGER_RE,
    QueryRewriter,
    campus_search_keywords,
    normalize_school_terms,
    official_site_query,
    temporal_anchor,
    wechat_query,
)
from utils.logger import get_logger

from xiaowo_web.evidence.wechat import WechatClient

log = get_logger(__name__)
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


# ── smart 来源准入（2026-09-16）─────────────────────────────────
# 实测百度智能搜索对长尾查询会返回**软件下载站、文库聚合站**，甚至**彩票/赌博/成人
# 内容站**，而它们会被当作「来源」展示给用户。故只让够格的域名进入 sources：
#   official      `*.ustc.edu.cn`              → official_primary（官方一手）
#   authoritative 政府/国家级媒体白名单          → reliable_independent（独立可靠）
#   third_party   自媒体/聚合（百家号/知乎/CSDN/微信…） → 不展示
#   blocked       明显无关或不良                → 不展示
_AUTHORITATIVE_HOSTS = (
    "moe.gov.cn", "gov.cn", "xinhuanet.com", "news.cn", "people.com.cn",
    "cctv.com", "thepaper.cn", "chinadaily.com.cn", "gmw.cn", "china.com.cn",
    "chsi.com.cn",
)
_BLOCKED_DOMAIN_RE = re.compile(
    r"(?:2265\.com|docin\.com|doc88\.com|book118\.com|renrendoc|onlinedown|"
    r"downcc|cr173|ddooo|pc6\.com|xiazai|caipiao|liuhecai|11xuan5|shuangseqiu|"
    r"casino|bet365|porn|ero)", re.IGNORECASE,
)
_BLOCKED_TITLE_RE = re.compile(
    r"(?:十一选五|双色球|大乐透|彩票|开奖|走势图|棋牌|真人视讯|老虎机|"
    r"エロ|成人|色情|porn|casino)", re.IGNORECASE,
)


# 正文里"看起来像引用"的不合格站名。提示词拦不住——模型会照着自己搜到的网页写
# "来源：2265下载网、豆丁下载网"（实测两次都写了），所以必须**确定性删除**（2026-09-16）。
_UNRELIABLE_SOURCE_TEXT_RE = re.compile(
    r"(?:2265|豆丁|道客|文库|下载网|下载站|开奖|彩票|十一选五|双色球|大乐透|"
    r"走势图|棋牌|真人视讯|老虎机|成人|色情|エロ|docin|doc88|book118|caipiao)",
    re.IGNORECASE,
)
_ANSWER_SOURCE_LINE_RE = re.compile(
    r"^[ \t]*(?:来源|资料来源|信息来源|参考来源)[：:].*$", re.MULTILINE,
)


# 命中的**整段**站名（以标点为界）——用来把内联提到的不合格站点替换掉，而不是只删整行。
_JUNK_CHUNK_RE = re.compile(
    r"[^，。；、,.;\s「」“”\"'（）()\[\]]*?"
    r"(?:2265|豆丁|道客|文库|下载网|下载站|开奖|彩票|十一选五|双色球|大乐透|走势图|"
    r"棋牌|真人视讯|老虎机|成人|色情|エロ|docin|doc88|book118|caipiao)"
    r"[^，。；、,.;\s「」“”\"'（）()\[\]]*"
)


def _scrub_unreliable_sources(text: str) -> str:
    """清理正文里的**不合格站点痕迹**（确定性，不依赖提示词）。

    为什么必须后处理：提示词拦不住——加了"不得引用无关站点"规则后，模型仍照写
    "来源：2265下载网"，因为它是在**描述自己的检索输入**（2026-09-16 实测两次）。

    两步：
    1. 删掉列举不合格站点的「来源：」行；
    2. 把**内联**提到的不合格站名整段替换为「无关站点」（如"如'贵州十一选五开奖结果'等"
       → "如'无关站点'等"），避免只清整行而漏掉夹在句中的例子。
    """
    def _drop(match: "re.Match[str]") -> str:
        return "" if _UNRELIABLE_SOURCE_TEXT_RE.search(match.group(0)) else match.group(0)

    cleaned = _ANSWER_SOURCE_LINE_RE.sub(_drop, text or "")   # ① 先删整行
    cleaned = _JUNK_CHUNK_RE.sub("无关站点", cleaned)          # ② 再清内联
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


# 检索结果**全是无关/低质站点**时的固定答复（B3 收窄版）。
# 为什么不靠提示词/关键词表：即便加了"不得引用无关站点"并清理了「来源：」行，模型仍会在
# 正文里**举例复述**垃圾站（实测"问鼎国际(登录入口)APP下载"漏网），站名表永远清不干净。
_NO_RELIABLE_SOURCE_ANSWER = (
    "本次联网检索没有找到与这个问题相关的可靠来源（检索结果均为无关或低质站点），"
    "小蜗暂时无法确认。\n\n"
    "建议换个更具体的问法，或通过官方渠道核实（学校官网、教务处，或学院教学秘书）。"
)


def _smart_source_tier(host: str, title: str) -> str:
    """smart 来源准入分层：official / wechat_unverified / authoritative / third_party / blocked。

    ⚠️ 微信公众号**拿不到账号名**：实测 references 只有 content/date/icon/id/image/
    title/type/url/video/web_anchor/website 十个字段，微信链接的 `website` 恒为
    "腾讯网"。所以 mp.weixin.qq.com **不能**当权威——只能"展示但标未核实"，绝不
    用文章标题去猜账号（那是制造假权威）。官方公众号的认定留在**能拿到账号名**的
    自家公众号渠道（`evidence/wechat.py::is_official_account`，来源是 `og:article:author`）。
    """
    host = (host or "").lower()
    if host == "ustc.edu.cn" or host.endswith(".ustc.edu.cn"):
        return "official"
    if _BLOCKED_DOMAIN_RE.search(host) or _BLOCKED_TITLE_RE.search(title or ""):
        return "blocked"
    if host in {"mp.weixin.qq.com", "weixin.qq.com"} or host.endswith(".mp.weixin.qq.com"):
        return "wechat_unverified"
    for allowed in _AUTHORITATIVE_HOSTS:
        if host == allowed or host.endswith("." + allowed):
            return "authoritative"
    return "third_party"


# 摘要兜底的最小长度（2026-09-17 复核：60 → 200）。
# 实测偏短的返回基本都是**导航/列表页**——例如 66/77 字的「公告 公告 公告 图书馆2023寒假
# 开放安排…」、28 字的「AIP ebook 正式 AMS…电子书」，换检索模式也救不了，不该进审核库。
# 正常内容页的摘录（纯搜索模式）在 1000~1499 字，200 的门槛不会误伤。
_SNIPPET_MIN_CHARS = 200

# 过于宽泛的 2 字片段：几乎任何校园标题都会命中，留着就会把无关文章判成"相关"
# （2026-09-17 收紧公众号相关性过滤时加的，见 _wechat_core_words）
_WECHAT_GENERIC_WORDS = frozenset({
    "专业", "学院", "大学", "学校", "学生", "同学", "时间", "通知", "信息",
    "相关", "工作", "政策", "考试", "安排", "报名", "招生", "活动", "新生",
    "关于", "最新", "官方", "中国", "科学", "技术", "教师", "校园", "中心",
})

# 「合成结果无法核验」的专用拒答文案（2026-09-17）：与 B3 的"没有合格来源"不同——
# 这里检索是有结果的，只是合成出来的数字/日期在检索结果里找不到依据，所以不展示。
def _pure_answers(bundle: object) -> bool:
    """纯搜索是否给出了**可用答案**（2026-09-17 晚）。

    拒答/证据不足**不算** —— 那种情况要留给公众号兜底（它的定位已从"优先"改为"安全网"）。
    """
    return bundle is not None and getattr(bundle, "terminal_reason", "") == "AI_GENERATED"


_UNVERIFIED_ANSWER = (
    "小蜗这次**没有查到可靠的数据**可以回答这个问题——检索到的资料里没有能核实的相关数字"
    "或日期，小蜗不凭印象补充。\n\n"
    "建议换个更具体的问法，或通过官方渠道确认（学校官网、教务处，或学院教学秘书）。"
)


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
        on_delta: Callable[[str], None] | None = None,
        local_hint: dict | None = None,
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

        # ── 公众号分支 与 纯搜索**并行**（2026-09-17）──
        # 量得公众号 collect 固定约 9.7s（搜狗检索+取 3 篇正文），而它多数情况**不会确认**
        # 答案（确定性证据门槛很严），串行就等于每个"科大"问题白等这 9.7s 再跑纯搜索。
        # 并行后总耗时 ≈ max(两者) 而非相加：实测 19.7s → 约 12s。
        # 优先级不变：公众号一旦确认答案就立刻用它（本校官方号内容更可信），并取消纯搜索。
        wechat_task = None
        pure_task = None
        pure_limitations: list[str] = []
        if (
            self.wechat is not None
            and self.settings.wechat_enabled
            and WECHAT_TRIGGER_RE.search(sanitized.text)
        ):
            wechat_task = asyncio.create_task(
                self._wechat_branch(sanitized.text, year_anchor, on_stage)
            )
        if (
            self.settings.web_answer_mode == "pure"
            and self.settings.search_provider == "baidu"
        ):
            pure_task = asyncio.create_task(
                self._pure_search_answer(
                    sanitized.text, pure_limitations, [], on_stage, on_delta=on_delta,
                    local_hint=local_hint,
                )
            )
        # ── 取舍顺序（2026-09-17 晚，实测后调整）──
        # 纯搜索通常 4~7s 就有答案；公众号要 ~13s（抓 3 篇正文，每篇 2.7~3.7s）且只有
        # 25% 会确认，而**确认的那些题本地知识库也都能答**（生产本地优先根本走不到联网）。
        # 所以：纯搜索答得出来 → **立刻答**（取消公众号，不白等）；
        #       纯搜索答不出来 → **才等公众号**，把它放在真正有价值的位置（兜底安全网）。
        wechat_sources: list[dict] = []
        pure = None
        if pure_task is not None:
            pure = await pure_task
            limitations_acc.extend(pure_limitations)
            if _pure_answers(pure):
                if wechat_task is not None:
                    wechat_task.cancel()
                    await asyncio.gather(wechat_task, return_exceptions=True)
                return pure
        if wechat_task is not None:
            confirmed, wechat_sources, notes = await wechat_task
            limitations_acc.extend(notes)
            if confirmed is not None:
                if pure_task is not None:
                    # 用 gather(return_exceptions=True) 收掉子任务的取消结果：
                    # 不要用 `except (CancelledError, Exception)` —— 那会连**外层**任务的
                    # 取消一起吞掉（超时/断流时就无法正常中止了）。
                    pure_task.cancel()
                    await asyncio.gather(pure_task, return_exceptions=True)
                return confirmed
        if pure is not None:
            # 纯搜索给的是拒答（证据不足）→ 交给 runner 回退本地。
            # 拒答 bundle 的 sources 为空是**有意的**（闸门拒答不展示无依据内容），
            # 所以这里也不补公众号来源，免得"没查到可靠数据"旁边又列出一堆未核实来源。
            if pure.terminal_reason != "EVIDENCE_INSUFFICIENT":
                # 并行时纯搜索拿不到公众号来源（它先起跑）→ 在这里补上，去重后并入
                seen = {str(item.get("source_id") or "") for item in pure.sources}
                for item in wechat_sources:
                    if str(item.get("source_id") or "") not in seen:
                        pure.sources.append(item)
            return pure


        # ── 2026-09-07 百度智能搜索生成直达答复（smart 模式）：提示词工程直接返回答案 ──
        # 覆盖场景：搜索→抓取→提取→置信门链路对时事类常因 robots/渲染失败空手而归；
        # smart 由百度云端完成检索+生成，一次返回答案（无引用，来源标注提醒）。
        # 仅 provider=baidu 且 web_answer_mode=smart 时启用；失败自动回退经典链路。
        if (
            self.settings.web_answer_mode == "pure"
            and self.settings.search_provider == "baidu"
        ):
            pure = await self._pure_search_answer(
                sanitized.text, limitations_acc, wechat_sources, on_stage, on_delta=on_delta,
                local_hint=local_hint,
            )
            if pure is not None:
                return pure

        if (
            self.settings.web_answer_mode == "smart"
            and self.settings.search_provider == "baidu"
        ):
            smart = await self._smart_answer(
                sanitized.text, limitations_acc, wechat_sources, on_stage,
            )
            if smart is not None:
                return smart

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

    async def _wechat_branch(
        self,
        question: str,
        year_anchor,
        on_stage: StageCallback | None,
    ) -> tuple[AnswerBundle | None, list[dict], list[str]]:
        """公众号优先分支（2026-09-05 起）。

        返回 `(确认答案|None, 未确认但保留的公众号来源, 限制说明)`。
        抽成独立方法是为了能和纯搜索**并行**执行（2026-09-17，见 `answer`）。
        """
        notes: list[str] = []
        self._stage(on_stage, "web_search", "正在检索微信公众号")
        try:
            bundle = await asyncio.wait_for(
                # 微信查询改写：官方名称词+业务词（原文长句在搜狗微信索引匹配差 → 噪音/漏命中）
                self.wechat.collect(wechat_query(question)),
                timeout=max(15.0, min(25.0, self.settings.run_timeout_seconds * 0.4)),
            )
        except asyncio.TimeoutError:
            return None, [], ["微信公众号检索超时，已回退通用检索。"]
        if bundle is None or not bundle.articles:
            return None, [], notes
        pages = await self._wechat_pages(bundle.articles)
        if pages:
            # 2026-09-05 相关性过滤：非官方号文章标题必须命中查询核心词（去官方名），
            # 否则丢弃（搜狗对"中科大 x"类常返回标题含"中科大"但内容无关的公众号）
            business_words = self._wechat_core_words(question)
            if business_words:
                kept = [
                    p for p in pages
                    if p.trust.rule_id == "wechat_official"
                    or any(w in str(p.page.title or "") for w in business_words)
                ]
                dropped = len(pages) - len(kept)
                pages = kept
                if dropped > 0:
                    notes.append(f"微信公众号命中 {dropped} 条与问题无关的内容，已忽略。")
        if not pages:
            return None, [], notes
        confirmed, _claims = await self._assess_and_answer(
            pages, question, notes, year_anchor, on_stage,
        )
        if confirmed is not None:
            return confirmed, [], notes
        # 2026-09-05 体验放宽：公众号已查看但未达门槛 → 保留微信来源，继续通用互联网搜索
        kept_sources = [
            self._public_source(record, index + 1) for index, record in enumerate(pages)
        ]
        notes.append("已查看公众号内容，但尚无声明达到确定性证据门槛；继续检索互联网公开页面。")
        return None, kept_sources, notes

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
        """微信命中相关性词：去官方名与虚词后，按**内容片段**取 2 字窗口。

        2026-09-17 收紧（原来是直接在整句上滑 2 字窗口）：

        - 「转专业政策是怎样的」会产出「是怎」「样的」这种**跨虚词**碎片；
        - 「专业」「政策」这类**过泛词**几乎命中任何校园标题 —— 实测「转专业政策」
          会被「各院系专业分数线」判成相关，只要有两个字相同就算数。

        现在先删虚词/疑问词（窗口不再跨它们生成），再滑窗并丢掉泛词；命中判定仍是
        "标题含任一窗口"，但留下的窗口都是能区分的业务词。
        """
        text = question or ""
        for name in ("中国科学技术大学", "中科大", "中国科大", "USTC", "科大"):
            text = text.replace(name, "")
        # 先删疑问/人称/助词，避免窗口跨它们生成（长词必须排在短词前）
        for junk in (
            "怎么样", "怎么办", "是不是", "有没有", "能不能", "什么事",
            "怎样", "怎么", "什么", "多少", "何时", "哪里", "如何", "哪个", "哪些",
            "多久", "啥", "请问", "一下", "我们", "你们", "相关", "有关", "目前",
            "是", "的", "了", "吗", "呢", "吧", "啊", "呀",
            "？", "?", "，", ",", "。", "、", "！", "!", "：", ":", "；", ";",
            "（", "）", "(", ")",
        ):
            text = text.replace(junk, " ")
        words: list[str] = []
        for seg in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for i in range(len(seg) - 1):
                w = seg[i:i + 2]
                if w not in _WECHAT_GENERIC_WORDS:
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
    def _smart_prompt(question: str) -> str:
        """智能搜索的**单条 user 内容**：检索关键词在前、**最少必要指令**在后。

        2026-09-16 砍掉全部冗余指令。依据是实测：该端点不支持 system 角色，整条消息
        **都会参与检索**，所以写进去的每一条"格式要求"都是**检索噪音**。同一批问题
        （转专业/国庆/天气/新闻/助学贷款）对照：

        - 全套规则（729 字）→ 8 条引用**全是 `www.docin.com` 文库垃圾站**，本校 0 条；
        - 极简指令 → 命中本校官网 / 气象官方站。

        现在只留两条**后处理补不回来**的防幻觉规则：
        1. 只依据检索结果（日期/数字不得推算）—— 防幻觉
        2. 无关就说「未找到相关信息」—— 防答成其他学校/对象

        其余约束（长度、结论先行、不要表格、来源行）交给后处理与前端渲染。
        另：`site:` 对该端点无效（实测 0/8），不得写进检索词。
        """
        keywords = campus_search_keywords(question)
        parts: list[str] = []
        if keywords:
            parts.append("检索关键词：" + " ".join(keywords))
            parts.append("---")
        parts.append(
            "只依据检索结果回答：每个日期、数字、名称都必须在检索结果里有依据，"
            "不得用你自己的知识补充或推算。"
        )
        if keywords:
            parts.append(
                "若检索结果与本问题无关、或没有任何中国科学技术大学的官方来源，"
                "直接回答「未找到本校相关信息」，不得改答其他学校或其他对象。"
            )
        else:
            parts.append("若检索结果与本问题无关，直接回答「未找到相关信息」。")
        parts.append(f"用户问题：\n{question}")
        return "\n".join(parts)

    async def _smart_answer(
        self,
        question: str,
        limitations_acc: list[str],
        wechat_sources: list[dict],
        on_stage: StageCallback | None,
    ) -> AnswerBundle | None:
        """百度智能搜索生成直达答复；返回 None 表示失败（由调用方回退经典链路）。"""
        self._stage(on_stage, "web_search", "正在使用百度智能搜索生成")
        timeout = 60.0  # 智能搜索生成（128k 模型）需 5~15s，独立于 8s 搜索超时
        try:
            # 提示词已含检索关键词与全部指令 → 作为单条 user 内容传入
            text, references = await asyncio.wait_for(
                self.search.generate(
                    self._smart_prompt(question),
                    model=self.settings.baidu_smart_model,
                ),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 —— 生成失败即回退经典检索链（含 429）
            limitations_acc.append("百度智能搜索生成暂不可用，已回退通用检索。")
            return None
        text = _scrub_unreliable_sources(text)
        return self._references_bundle(
            question=question,
            answer_text=text,
            references=references,
            limitations_acc=limitations_acc,
            wechat_sources=wechat_sources,
            origin_note=(
                "来自百度智能搜索生成（AI 检索）；未附独立引用，请以教务系统为准。"
                "未附独立引用，请以教务系统为准。"
            ),
        )

    def _references_bundle(
        self,
        *,
        question: str,
        answer_text: str,
        references: list[dict],
        limitations_acc: list[str],
        wechat_sources: list[dict],
        origin_note: str,
        refusal: bool = False,
    ) -> AnswerBundle:
        """把「答案文本 + 本轮 references」组装成 AnswerBundle（两条联网路径共用）。

        包含来源准入分层、B3（无合格来源且确过滤了垃圾站 → 整篇替换为诚实答复）、
        B4（校内问句无官方来源必须示警）、进料 URLs/摘要透传。2026-09-17 从
        `_smart_answer` 抽出，供「纯搜索 + 自研合成」复用，避免两套逻辑漂移。
        """
        # B1/B2：接住**真实返回的 references** 并按域名分层。
        # 原实现丢弃 references、硬造一条 qianfan.baidubce.com 的假来源（2026-09-16 修）。
        # 进料（2026-09-16）：把 references 的 URL 交给上层后台任务抓整页入库。
        # 这里只传 URL —— 入库要整页正文，而抓取不能阻塞回答。去重、限流都在下游。
        ref_urls = [str(r.get("url") or "").strip() for r in references]
        ref_urls = [u for u in ref_urls if u]
        # url → references 自带的 {content,title}：给 robots 禁抓站点做摘要兜底（2026-09-17）
        ref_snippets = {
            u: {
                "content": str(ref.get("content") or "").strip(),
                "title": str(ref.get("title") or "").strip(),
            }
            for ref, u in ((r, str(r.get("url") or "").strip()) for r in references)
            if u
        }
        sources, official_n, dropped = self._smart_sources(references)
        # B3（收窄版，2026-09-16）：**没有任何合格来源、且确实过滤掉了 blocked 站**
        # → 说明本轮检索结果全是无关/低质站点，整篇替换为固定诚实答复，不复述检索垃圾。
        # 收窄在 `dropped["blocked"]`：只有第三方（如知乎/百家号）而不含垃圾时**不替换**，
        # 那种情况仍可能有可用内容。
        if not sources and dropped["blocked"]:
            limitations_acc.append(
                f"本次检索到 {dropped['blocked']} 条无关或不良站点、无可采信来源，"
                "已改为固定答复，不复述检索内容。"
            )
            return AnswerBundle(
                markdown=_NO_RELIABLE_SOURCE_ANSWER,
                claims=[{
                    "claim_id": "c1", "text": _NO_RELIABLE_SOURCE_ANSWER,
                    "kind": "factual", "status": "insufficient", "evidence": [],
                }],
                sources=[],
                ingestion_urls=ref_urls,
                ingestion_snippets=ref_snippets,
                web_references=references,
                limitations=limitations_acc,
                terminal_reason="EVIDENCE_INSUFFICIENT",
            )
        if not sources:
            # references 为空时的兜底来源（仍标未核实，绝不伪装成官方）
            sources.append({
                "source_id": "s-ai-search", "title": "百度智能搜索生成",
                "display_url": None, "institution": "百度智能云 · AI 搜索",
                "domain": "qianfan.baidubce.com", "published_at": None,
                "fetched_at": None, "level": "unverified", "validity": "unverified",
                "citation": 1, "tags": ["ai_generated"],
            })
        seen = {str(item.get("source_id") or "") for item in sources}
        for item in wechat_sources:
            if str(item.get("source_id") or "") not in seen:
                sources.append(item)
        limitations_acc.append(origin_note)
        limitations_acc.append(f"本次命中本校官方来源 {official_n} 条。")
        if dropped["blocked"]:
            limitations_acc.append(
                f"本次检索到 {dropped['blocked']} 条无关或不良站点，"
                "已过滤、未作为来源展示。"
            )
        if dropped["third_party"]:
            limitations_acc.append(
                f"另有 {dropped['third_party']} 条自媒体/聚合类站点，"
                "未达到来源标准，未作为来源展示。"
            )
        wechat_n = sum(1 for item in sources if "wechat_unverified" in (item.get("tags") or []))
        if wechat_n:
            limitations_acc.append(
                f"其中 {wechat_n} 条为微信公众号文章：智能搜索未提供账号名，无法核实发布方，"
                "已标注为未核实来源，请以官方渠道为准。"
            )
        # 闸门拒答（2026-09-17）：与 B3 一样**不展示来源**，并把终态标成 EVIDENCE_INSUFFICIENT
        # —— 这一步至关重要：runner 的本地回退是以该终态为条件的（实测漏掉会把
        # 88 字的拒答直接推给用户，而不是回退本地知识库）。
        if refusal:
            return AnswerBundle(
                markdown=answer_text,
                claims=[{
                    "claim_id": "c1", "text": answer_text,
                    "kind": "factual", "status": "insufficient", "evidence": [],
                }],
                sources=[],
                ingestion_urls=ref_urls,
                ingestion_snippets=ref_snippets,
                web_references=references,
                limitations=limitations_acc,
                terminal_reason="EVIDENCE_INSUFFICIENT",
            )
        # B4：校内事务问句若一条官方来源都没有，必须显式示警
        if official_n == 0 and campus_search_keywords(question):
            limitations_acc.append(
                "本次联网检索**未命中本校官方来源**，上述内容均来自第三方站点，"
                "请勿据此办理事务；相关事务请以教务系统或本校官方文件为准。"
            )
        return AnswerBundle(
            markdown=answer_text,
            claims=[{
                "claim_id": "c1",
                "text": answer_text,
                "kind": "factual",
                "status": "generated",
                "evidence": [],
            }],
            sources=sources,
            ingestion_urls=ref_urls,
            ingestion_snippets=ref_snippets,
            web_references=references,
            limitations=limitations_acc,
            terminal_reason="AI_GENERATED",
        )

    async def _pure_search_answer(
        self,
        question: str,
        limitations_acc: list[str],
        wechat_sources: list[dict],
        on_stage: StageCallback | None,
        on_delta: Callable[[str], None] | None = None,
        local_hint: dict | None = None,
    ) -> AnswerBundle | None:
        """纯搜索取原文摘录 → 两道闸门 → **自研合成**（2026-09-17，`web_answer_mode=pure`）。

        `local_hint`（2026-09-18）：本地已确认内容，作为合成时"必须保留的基础"并计入
        可核验证据 —— 复合问题（本地答一半、联网补一半）靠它避免本地那半段被覆盖。

        与 `_smart_answer` 的差别：端点**不做总结**（不传 `model`），把 1000~1500 字/条的
        原文摘录交给我们（实测：智能生成只给固定 203 字、耗时 22~43s；纯搜索 0.5~1s）。

        为什么答案要自己合成：端点那份答案是**黑盒**，实测其数字大量不在它披露给我们的
        证据里（可核验比例 0.342），我们无法校验；自己合成才能做下面两道闸门。

        1. **相关性闸门（合成前）**：refs 与问题无关就不合成。实测「食堂开放时间」这题
           纯搜索返回的全是教务处选课/竞赛，智能生成会靠自己的严格提示词拒答，而我们
           自己合成时必须显式提供同等保护——否则会像实测那样"垃圾证据也硬编"。
        2. **可核验性闸门（合成后）**：答案里的数字/日期必须都在 refs 里（归一化后比对，
           含全角/乘号），有一条不达标就拒答。

        任一道不过 → 与旧路径完全一致的 `_NO_RELIABLE_SOURCE_ANSWER` +
        `terminal_reason=EVIDENCE_INSUFFICIENT`，由 runner 回退本地知识库。
        """
        self._stage(on_stage, "web_search", "正在联网检索（纯搜索）")
        # ⚠️ 检索词用**原问题**，不要用 `campus_search_keywords`（2026-09-17 实测）：
        # 那套关键词是为**智能生成**模式设计的，会把「教务处」「官方」拼进去，导致纯搜索
        # 几乎全部命中"教务处首页/新闻"，把真正讲问题的页面挤出前 8 名。同一问题对比：
        #   「学籍证明怎么办」→ 关键词式：教务处首页+信息公开年报（无关）
        #                       原问题式：「离校生如何办理毕业证明、成绩证明及学位证明?」
        #   「食堂开放时间」  → 关键词式：选课通知/竞赛新闻（无关）
        #                       原问题式：「迎新特辑｜中国科学技术大学食堂攻略」
        # 原问题式在实测 4/4 题上命中质量明显更好。
        query = str(question or "").strip()[:120]
        try:
            references = await asyncio.wait_for(
                self.search.references_only(query, limit=8), timeout=30.0
            )
        except Exception as exc:  # noqa: BLE001 —— 失败交给调用方回退经典链路
            limitations_acc.append("联网检索暂不可用，已回退通用检索。")
            log.info(f"纯搜索失败: {exc}")
            return None
        references = [r for r in (references or []) if isinstance(r, dict)]
        if not references:
            limitations_acc.append("联网检索没有返回结果。")
            return None
        # 闸门一：相关性（判不出来按不相关处理 = fail-closed）
        relevant = await asyncio.to_thread(judge_relevance, question, references)
        if not relevant:
            limitations_acc.append(
                "本次检索到的内容与问题不相关，已改为固定答复；"
                "如需最新信息请以官方渠道为准。"
            )
            return self._references_bundle(
                question=question,
                answer_text=_NO_RELIABLE_SOURCE_ANSWER,
                references=references,
                limitations_acc=limitations_acc,
                wechat_sources=wechat_sources,
                origin_note="联网检索结果与问题不相关，未据此作答。",
                refusal=True,
            )
        # 合成 + 闸门二
        self._stage(on_stage, "answering", "正在依据检索结果生成回答")
        hedge_notes: list[str] = []
        try:
            answer, unsupported = await asyncio.to_thread(
                compose_and_verify, question, references, report=hedge_notes,
                on_delta=on_delta, local_hint=local_hint,
            )
        except Exception as exc:  # noqa: BLE001
            limitations_acc.append("联网答案合成失败，已回退通用检索。")
            log.info(f"自研合成失败: {exc}")
            return None
        # 对冲说明（"哪几处数字没依据"）要透出给用户，不能只写在正文里
        limitations_acc.extend(hedge_notes)
        if not answer.strip():
            limitations_acc.append("联网答案合成为空，未予采用。")
            return None
        if unsupported:
            limitations_acc.append(
                f"合成答案里有 {len(unsupported)} 处数字/日期无法在检索结果中核实"
                f"（如 {'、'.join(unsupported[:3])}），已改为固定答复，不展示无依据内容。"
            )
            return self._references_bundle(
                question=question,
                answer_text=_UNVERIFIED_ANSWER,
                references=references,
                limitations_acc=limitations_acc,
                wechat_sources=wechat_sources,
                origin_note="合成答案存在无法核实的数字/日期，未予采用。",
                refusal=True,
            )
        return self._references_bundle(
            question=question,
            answer_text=answer,
            references=references,
            limitations_acc=limitations_acc,
            wechat_sources=wechat_sources,
            origin_note=(
                "来自联网检索（百度纯搜索的原文摘录）+ 小蜗自研合成；"
                "每个日期与数字均可在下方来源中核对。"
            ),
        )

    @staticmethod
    def _url_key(url: str) -> str:
        """URL 归一化键：忽略 scheme、末尾斜杠与 fragment，便于与纯搜索结果对齐。"""
        parts = urlsplit(str(url or "").strip())
        host = (parts.netloc or "").lower()
        path = (parts.path or "").rstrip("/")
        return f"{host}{path}?{parts.query}" if parts.query else f"{host}{path}"

    async def _rich_snippet(
        self, url: str, title: str, cache: dict[tuple[str, str], str]
    ) -> str:
        """对抓不到正文的 URL，用**纯搜索模式**取更长摘录（2026-09-17）。

        - 查询词用**页面自身标题**（不是用户问题）：既能精准命中该页，也不把用户上下文
          带进后台任务（"审核队列不得保存用户问题"的约定继续成立）。
        - 加 `search_filter.match.site=[host]` 提高精度（仅 v2 生效）。
        - **只认 URL 完全一致的返回**（归一化后），拿不到就返回空串，绝不拿同站别的页面顶替。
        - 任何失败都静默返回空串：这是后台补料，不能影响已经返回给用户的回答。
        """
        target = self._url_key(url)
        if not target:
            return ""
        host = urlsplit(str(url or "")).netloc.lower()
        query = (title or "").strip()[:120] or str(url or "")[:120]
        key = (query, host)
        if key in cache:
            return cache[key]
        cache[key] = ""      # 先占位，避免同一批里重复请求
        fetch = getattr(self.search, "references_only", None)
        if fetch is None:    # 非百度 provider（searxng/bocha）没有这个能力
            return ""
        try:
            refs = await fetch(query, site=host, limit=8)
        except Exception as exc:  # noqa: BLE001
            log.info(f"纯搜索补摘要失败 {url[:70]}: {exc}")
            return ""
        for ref in refs:
            if self._url_key(str(ref.get("url") or "")) == target:
                content = str(ref.get("content") or "").strip()
                cache[key] = content
                return content
        return ""

    async def fetch_candidates_for_ingestion(
        self, urls: list[str], *, snippets: dict[str, dict[str, str]] | None = None,
        limit: int = 8, min_chars: int = 200,
    ) -> list[dict]:
        """把联网答案引用的 URL 抓成**可入审核库的候选**（供上层后台任务调用）。

        为什么必须单独抓一次：smart 的 `references` 只给 `content` 片段，而
        `ReviewStore.enqueue_candidate` 硬性要求 `snapshot_text`（整页正文）+
        `evidence_span_hash`。这里复用**与经典链路同一套安全轨道**
        （`url_guard` 校验 → Crawl4AI 抓取 → `trust_store` 分级），只补齐这两个字段。

        逐条失败只跳过、绝不抛出：这是后台补料，不能影响已经返回给用户的回答。

        **摘要兜底（2026-09-17）**：有些站点 robots.txt 明令禁止抓取，抓正文必然失败
        ——实测 `mp.weixin.qq.com` 是 `Disallow: /`（Crawl4AI 直接拒，适配器把它归一化
        成 502），微博同因；`kepu.ustc.edu.cn` 则是抓到但无可提取文本。这些站点我们
        **不绕 robots**，改用检索器随 references 返回的 `content` 摘要入审核库，并
        **显式标注** `content_type=text/search-snippet` + 标题后缀「仅搜索摘要」，
        由人工审批时自行判断——**绝不冒充整页正文**。
        """
        candidates: list[dict] = []
        seen: set[str] = set()
        # 内容指纹去重：实测同一份天气预报被 6 个不同子域返回（weather.com.cn / baidu. /
        # wap. / uc. / e. …），只按 URL 去重会把 6 份近重复文档全灌进审核队列
        # （2026-09-16 实测 6/8 条候选实为同一页）。用抓取结果的 content_hash 兜住。
        seen_content: set[str] = set()
        # 纯搜索补摘要的缓存：同一 (标题, 站点) 只请求一次
        rich_cache: dict[tuple[str, str], str] = {}
        snippet_map = {
            str(k).strip(): {
                "content": str((v or {}).get("content") or "").strip(),
                "title": str((v or {}).get("title") or "").strip(),
            }
            for k, v in (snippets or {}).items()
        }
        for raw in (urls or [])[:limit]:
            url = str(raw or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                validated = await asyncio.to_thread(self.url_guard.validate, url)
            except Exception as exc:  # noqa: BLE001 —— URL 不安全：**绝不**走摘要兜底
                log.info(f"进料 URL 校验跳过 {url[:80]}: {exc}")
                continue
            # 摘要兜底也要有机构分级：直接按**已通过校验的原始 URL**分级
            trust = self.trust_store.classify(validated)
            page = None
            final_url = validated.normalized_url
            try:
                crawled = await self.crawler.crawl(validated.normalized_url)
            except Exception as exc:  # noqa: BLE001 —— 单条失败不影响其余
                log.info(f"进料抓取跳过 {url[:80]}: {exc}")
                crawled = None
            if crawled is not None:
                try:
                    # 重定向后的最终域也必须过安全校验，否则**整条丢弃**（不能退化成
                    # 用原始 URL 的摘要把一次被拒的跳转悄悄放进来）
                    final = await asyncio.to_thread(self.url_guard.validate, crawled.final_url)
                except Exception as exc:  # noqa: BLE001
                    log.info(f"进料重定向目标不安全，整条跳过 {url[:80]}: {exc}")
                    continue
                trust = self.trust_store.classify(final)
                final_url = final.normalized_url
                page = crawled
            text = str(getattr(page, "markdown", "") or "")
            fingerprint = str(getattr(page, "content_hash", "") or "")
            if len(text) < min_chars:
                # 抓不到正文（robots 禁抓／抓到空页／过短导航页）→ 检索摘要兜底
                snippet = snippet_map.get(url) or {}
                body = str(snippet.get("content") or "")
                # 2026-09-17：先用**纯搜索模式**取更肥的原文摘录（实测 203 → 1000+ 字，
                # 0.5~1s，同样不抓正文、不碰 robots）。拿不到就沿用智能模式的短摘要。
                rich = await self._rich_snippet(
                    url, str(snippet.get("title") or ""), rich_cache
                )
                if len(rich) > len(body):
                    body = rich
                if len(body) < _SNIPPET_MIN_CHARS:
                    continue
                text = body
                fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
                title = str(snippet.get("title") or final_url)
                title = f"{title}（仅搜索摘要 {len(body)} 字，未抓正文）"
                content_type = "text/search-snippet"
            else:
                title = str(getattr(page, "title", "") or final_url)
                content_type = str(getattr(page, "content_type", "") or "text/html")
            if fingerprint:
                if fingerprint in seen_content:
                    continue
                seen_content.add(fingerprint)
            candidates.append({
                "snapshot_text": text,
                "evidence_span_hash": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                "source_id": "ref-" + hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:12],
                "normalized_url": final_url,
                "final_url": final_url,
                "title": title[:200],
                "institution": trust.institution,
                "level": trust.level,
                "fetched_at": (getattr(page, "fetched_at", None)
                               or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                "content_type": content_type,
            })
        return candidates

    @staticmethod
    def _smart_sources(references: list[dict]) -> tuple[list[dict], int, dict[str, int]]:
        """智能搜索的 `references` → **只保留够格的来源**（B1/B2 + 来源准入）。

        不够格的**不进 `sources`**，只计数，由调用方在 limitations 里如实说明——
        把下载站/赌博站当来源展示既误导也不安全（2026-09-16 实测踩到）。

        微信公众号（`wechat_unverified`）是**中间态**（2026-09-17 用户决定）：账号名拿不到
        → 进 `sources` 但 `level/validity=unverified`、**不计入 `official_n`**。

        Returns:
            (sources, 官方来源条数, {"third_party": n, "blocked": n})
        """
        sources: list[dict] = []
        official_n = 0
        dropped = {"third_party": 0, "blocked": 0}
        for index, ref in enumerate(references[:8], start=1):
            url = str(ref.get("url") or "").strip()
            if not url:
                continue
            host = urlsplit(url).netloc.lower()
            title = str(ref.get("title") or "").strip()
            tier = _smart_source_tier(host, title)
            if tier in dropped:
                dropped[tier] += 1
                continue
            is_official = tier == "official"
            is_wechat = tier == "wechat_unverified"
            if is_official:
                official_n += 1
            if is_official:
                level, institution = "official_primary", "中国科学技术大学"
                validity, tags = "active", ["ai_generated", "official"]
            elif is_wechat:
                # 拿不到账号名 → **显示但标未核实**，且不计入 official_n
                level, institution = "unverified", "微信公众号（账号未核实）"
                validity, tags = "unverified", ["ai_generated", "wechat_unverified"]
            else:
                level = "reliable_independent"
                institution = str(ref.get("website") or "").strip() or host
                validity, tags = "active", ["ai_generated", "authoritative"]
            sources.append({
                "source_id": f"s-smart-{index}",
                "title": title[:120] or host or "网页",
                "display_url": url,
                "institution": institution,
                "domain": host or None,
                "published_at": str(ref.get("date") or "").strip() or None,
                "fetched_at": None,
                "level": level,
                "validity": validity,
                "citation": index,
                "tags": tags,
            })
        return sources, official_n, dropped

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
