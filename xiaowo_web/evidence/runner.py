"""Automatic local-first gate with a strict Web evidence fallback."""

from __future__ import annotations

import asyncio
import inspect
import re

from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.chat.runner import QaRunner, chitchat_reply, is_chitchat_query
from xiaowo_web.evidence.pipeline import EvidencePipeline


_CURRENT_TERMS = re.compile(r"(?:最新|最近|近期|近日|最近几天|今天|现在|当前|截至|刚刚|本周|本月|今年|目前|现行|还有效吗)")


_TOOL_INTENTS = frozenset({
    "查成绩", "查课表", "查考试", "查空教室", "日程查询", "日程管理",
    "选课冲突", "退补选评估", "课程搜索", "选课推荐", "教师点评",
})


def _likely_local_tool_answer(question: str) -> bool:
    """预判：意图为个人数据/课程工具类 → 本地工具结果权威，无需并发联网。"""
    try:
        from knowledge.intent_classifier import classify
        return (classify(question or "").get("intent") or "") in _TOOL_INTENTS
    except Exception:
        return False


# 需要「用户本人数据」的问句特征：第一人称领属 + 自己的课程/成绩。
# **不能用 `_likely_local_tool_answer`**——它是给并行预判用的启发式，实测对
# "今天的新闻" 也返回 True，会把本该联网的时效问题误拦（2026-09-16 踩到）。
_PERSONAL_NEED_KW = ("我的", "我目前", "我现在", "我选的", "我修的", "我已修",
                     "帮我对比我", "对比下我")
# 本地回答已经在要求登录 → 联网无从补足，它就是权威答案
_LOGIN_NEED_MARKERS = ("未登录", "没有登录", "请登录", "登录后", "需要登录")


# 匿名短路里用来判「这题在问**外部时效事实**」的更窄时效词。
# **不含**"今天/现在/当前/目前"——它们在个人问句里常是"我现在的课表""我目前选的课"，
# 与"外部信息是否过期"无关；直接复用 `_CURRENT_TERMS` 会把"我目前选的课…"判成时效问题，
# 短路失效（2026-09-16 自查发现）。
_HARD_CURRENT_TERMS = re.compile(
    r"(?:最新|最近|近期|近日|截至|刚刚|本周|本月|今年|现行|还有效吗)"
)


def _needs_personal_data(question: str, local_markdown: str = "") -> bool:
    """问句是否在要「用户本人的数据」（未登录时联网根本查不到）。

    只在两种证据下判真：问句出现第一人称领属词，或本地回答已明确要求登录。
    通用政策类时效问题（"最新的转专业政策"）**不**算——那类正需要联网核验。"""
    q = question or ""
    if any(k in q for k in _PERSONAL_NEED_KW):
        return True
    return any(m in (local_markdown or "") for m in _LOGIN_NEED_MARKERS)


def _is_world_query(question: str) -> bool:
    """非校内通用常识判定（延迟导入 agents，避免启动期依赖图）。
    时效词（最新/今天/现状等）仍走联网证据链，避免世界知识给出过期信息。"""
    if _CURRENT_TERMS.search(question or ""):
        return False
    try:
        from agents.qa.nodes import is_world_knowledge_query
        return is_world_knowledge_query(question)
    except Exception:
        return False


def _world_answer(question: str) -> AnswerBundle:
    """世界知识回答（LLM 常识 + 免责标注）；失败降级固定文案。"""
    from agents.qa.nodes import world_knowledge

    try:
        result = world_knowledge({"query": question, "llm_down": False, "world_knowledge": True})
        answer = str(result.get("answer") or "").strip()
    except Exception:
        answer = ""
    if not answer:
        answer = "这是通用知识问题，小蜗暂时无法核实准确信息；你可以换个更具体的问题，或让我联网查询。"
    return AnswerBundle(
        markdown=answer,
        claims=[{
            "claim_id": "c1", "text": answer, "kind": "factual",
            "status": "insufficient", "evidence": [],
        }],
        sources=[],
        limitations=["通用信息，非联网核实，仅供参考。"] if "非联网核实" not in answer else [],
        terminal_reason="local_answer",
    )


class EvidenceAwareRunner:
    def __init__(self, local_runner: QaRunner, pipeline: EvidencePipeline) -> None:
        self.local_runner = local_runner
        self.pipeline = pipeline

    async def run(self, request: QaRunRequest) -> AnswerBundle:
        # 闲聊入口快路径：短问候句不进入联网证据链（模板回应，毫秒级）
        if is_chitchat_query(request.question):
            return chitchat_reply()
        if request.effective_mode == "local":
            return await self.local_runner.run(request)
        if request.effective_mode == "web":
            return await self.pipeline.answer(
                request.question,
                profile=request.principal.profile,
                on_stage=request.emit_stage,
            )

        # ── 本地优先（2026-09-16 改）──
        # 原先「时效词强制联网」（并行预起 + 末尾 needs_web 再压一次）会把本地官方内容
        # 顶掉：实测「最新的转专业政策是什么」本地 claim=confirmed、来源含
        # official_primary，却被换成了百度智能搜索生成的一段**天津科技大学**内容。
        # 现在：**本地答得出来就用本地**，联网只在本地区答不出时兜底。
        # 「纯联网」不再是可选模式，只保留为显式后门（effective_mode == "web"，
        # 由回答下方的「强制联网重答」触发）。
        world_query = _is_world_query(request.question)
        local = await self.local_runner.run(request)
        # 未登录 + 问句需要个人数据/校园工具 → 联网无从补足，本地的「请登录」才是权威答案。
        # 否则 local_ready 判 False（"请登录"类 claim 天然是 insufficient）会把这份好答案
        # 顶掉，换成网页泛泛科普（实测引到了极客公园/钛媒体）。2026-09-16。
        if (
            not request.principal.is_authenticated
            # 带时效词的问题（"最新/现行/目前…"）**照旧联网核验**：本地可能过期。
            # 否则"最新政策对我的影响"这类问句会被误拦（2026-09-16 自查发现）。
            and not _HARD_CURRENT_TERMS.search(request.question)
            and _needs_personal_data(request.question, local.markdown or "")
        ):
            note = "未登录：你的课程、成绩与培养方案需登录统一身份认证后读取。"
            if note not in local.limitations:
                local.limitations.append(note)
            return local
        # 本地可答判定：全部 claim 均 confirmed 即可（无论 kind）。
        local_ready = bool(local.claims) and all(
            claim.get("status") == "confirmed" for claim in local.claims
        )
        # 工具结果是校园实时系统直接返回的数据，比网页更权威且天然“最新”，
        # 不应被时效词送去联网核对。
        has_tool_source = any(
            (source.get("level") or "") in {"tool_result", "tool_cache"}
            for source in local.sources
        )
        # 本地答得出来 → 直接用本地（时效问句也一样：本地官方内容优先于联网）。
        # 时效问句用本地内容时如实标注，并指向「强制联网重答」这个出口。
        if local_ready:
            if _CURRENT_TERMS.search(request.question):
                note = ("以上依据本地知识库，可能不是最新；如需最新可点回答下方的"
                        "「强制联网重答」。")
                if note not in local.limitations:
                    local.limitations.append(note)
            return local
        # 世界知识通道（本地未命中 + 非校内通用常识 → LLM 直接答，跳过联网）
        if world_query:
            return _world_answer(request.question)

        # 本地答不出 → 串行联网兜底（不再预起：预起会在本地可答时白烧一次联网调用）
        web = await self.pipeline.answer(
            request.question,
            profile=request.principal.profile,
            on_stage=request.emit_stage,
            rounds_limit=1,
        )
        current = bool(_CURRENT_TERMS.search(request.question))
        # 联网证据不足时回退本地回答，不再丢弃已命中的本地结果。
        # 时效性问题且本地也未确认时，保留诚实拒答（不回退可能过期的数据）。
        fallback_eligible = (
            web.terminal_reason in {"EVIDENCE_INSUFFICIENT", "CRAWL_BLOCKED"}
            and local.terminal_reason == "local_answer"
            and bool(local.markdown.strip())
            and (has_tool_source or local_ready or not current)
        )
        if fallback_eligible:
            if has_tool_source:
                warning = "联网证据不足，已回退校园数据工具结果。"
            elif current:
                warning = "联网证据不足，以下为本地知识库回答（该问题可能涉及时效，请以官方最新信息为准）。"
            else:
                warning = "联网证据不足，已回退本地知识库回答。"
            if warning not in local.limitations:
                local.limitations.append(warning)
            return local
        return web

    async def close(self) -> None:
        close = getattr(self.local_runner, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        await self.pipeline.close()
