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
        current = bool(_CURRENT_TERMS.search(request.question))
        factual = [claim for claim in local.claims if claim.get("kind") == "factual"]
        local_confirmed = bool(factual) and all(claim.get("status") == "confirmed" for claim in factual)
        if local_confirmed and not current:
            return local
        return await self.pipeline.answer(
            request.question,
            profile=request.principal.profile,
            on_stage=request.emit_stage,
        )

    async def close(self) -> None:
        close = getattr(self.local_runner, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        await self.pipeline.close()
