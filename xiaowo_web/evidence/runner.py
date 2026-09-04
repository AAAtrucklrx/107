"""Automatic local-first gate with a strict Web evidence fallback."""

from __future__ import annotations

import inspect
import re

from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.chat.runner import QaRunner, chitchat_reply, is_chitchat_query
from xiaowo_web.evidence.pipeline import EvidencePipeline


_CURRENT_TERMS = re.compile(r"(?:最新|今天|现在|当前|截至|刚刚|本周|本月|今年|目前|现行|还有效吗)")


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

        local = await self.local_runner.run(request)
        # 本地可答判定：全部 claim 均 confirmed 即可（无论 kind）。
        # 修复：旧逻辑要求存在 factual claim（bool(factual)），导致寒暄/任务类回答
        #（kind=chitchat 等）被误判为未命中而无条件触发联网。
        local_ready = bool(local.claims) and all(
            claim.get("status") == "confirmed" for claim in local.claims
        )
        # 世界知识快速通道（2026-09-04）：本地未命中 + 非校内通用常识
        # → 直接 LLM 世界知识回答（含免责），不白白触发联网证据链。
        if not local_ready and _is_world_query(request.question):
            return _world_answer(request.question)
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
