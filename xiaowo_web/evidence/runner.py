"""Automatic local-first gate with a strict Web evidence fallback."""

from __future__ import annotations

import inspect
import re

from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.chat.runner import QaRunner
from xiaowo_web.evidence.pipeline import EvidencePipeline


_CURRENT_TERMS = re.compile(r"(?:最新|今天|现在|当前|截至|刚刚|本周|本月|今年|目前|现行|还有效吗)")


class EvidenceAwareRunner:
    def __init__(self, local_runner: QaRunner, pipeline: EvidencePipeline) -> None:
        self.local_runner = local_runner
        self.pipeline = pipeline

    async def run(self, request: QaRunRequest) -> AnswerBundle:
        if request.effective_mode == "local":
            return await self.local_runner.run(request)
        if request.effective_mode == "web":
            return await self.pipeline.answer(
                request.question,
                profile=request.principal.profile,
                on_stage=request.emit_stage,
            )

        local = await self.local_runner.run(request)
        # 本地可答判定：全部 claim 均 confirmed 即可（无论 kind）。
        # 修复：旧逻辑要求存在 factual claim（bool(factual)），导致寒暄/任务类回答
        #（kind=chitchat 等）被误判为未命中而无条件触发联网。
        local_ready = bool(local.claims) and all(
            claim.get("status") == "confirmed" for claim in local.claims
        )
        # 工具结果是校园实时系统直接返回的数据，比网页更权威且天然“最新”，
        # 不应被时效词送去联网核对。
        has_tool_source = any(
            (source.get("level") or "") in {"tool_result", "tool_cache"}
            for source in local.sources
        )
        current = bool(_CURRENT_TERMS.search(request.question))
        needs_web = current and not has_tool_source
        if local_ready and not needs_web:
            return local
        web = await self.pipeline.answer(
            request.question,
            profile=request.principal.profile,
            on_stage=request.emit_stage,
        )
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
