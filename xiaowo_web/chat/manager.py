"""Chat run ownership, deadlines, persistence, and terminal-state orchestration."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from xiaowo_web.auth.models import Principal
from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.chat.privacy import is_personal_query
from xiaowo_web.chat.runner import QaRunner
from xiaowo_web.errors import ApiError
from xiaowo_web.settings import WebSettings
from xiaowo_web.storage import WebStore


@dataclass(slots=True)
class _Job:
    request: QaRunRequest
    initial_limitations: list[str]


class ChatManager:
    def __init__(
        self,
        settings: WebSettings,
        store: WebStore,
        runner: QaRunner,
        ingestion_sink: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.runner = runner
        self.ingestion_sink = ingestion_sink
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._ingestion_tasks: set[asyncio.Task[None]] = set()

    async def create_run(
        self,
        *,
        principal: Principal,
        question: str,
        mode: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        if len(self._tasks) >= self.settings.max_concurrent_runs + self.settings.max_queued_runs:
            raise ApiError(503, "RUN_BUSY", "当前回答队列已满，请稍后重试。")
        personal = is_personal_query(question)
        if personal and not principal.is_authenticated:
            raise ApiError(401, "AUTH_REQUIRED", "个人学业问题需要先登录。")

        effective_mode = mode
        limitations: list[str] = []
        if personal and mode != "local":
            effective_mode = "local"
            limitations.append("个人数据问题已按隐私规则限制为本地处理，未发送到互联网。")

        history_owner = principal.history_owner_key
        if conversation_id and history_owner is None:
            raise ApiError(401, "AUTH_REQUIRED", "匿名会话只保存在当前浏览器。")
        if conversation_id and not self.store.conversation_belongs_to(conversation_id, history_owner or ""):
            raise ApiError(404, "CONVERSATION_NOT_FOUND", "没有找到该会话。")
        if history_owner and not conversation_id:
            conversation = self.store.create_conversation(history_owner, question)
            conversation_id = conversation["conversation_id"]

        history: list[dict[str, str]] = []
        if conversation_id and history_owner:
            conversation = self.store.get_conversation(conversation_id, history_owner)
            if conversation:
                history = [
                    {"role": item["role"], "content": item["content"]}
                    for item in conversation["messages"][-12:]
                ]

        run = self.store.create_run(principal.session_key, mode)
        request = QaRunRequest(
            run_id=run.run_id,
            question=question,
            requested_mode=mode,
            effective_mode=effective_mode,
            principal=principal,
            conversation_id=conversation_id,
            chat_history=history,
            emit_stage=lambda stage, message: self._stage(run.run_id, stage, message),
        )
        self.store.append_event(
            run.run_id,
            "run.created",
            {
                "requested_mode": mode,
                "effective_mode": effective_mode,
                "stage": "queued",
                "time_budget_seconds": self.settings.run_timeout_seconds,
                "conversation_id": conversation_id,
            },
        )
        task = asyncio.create_task(
            self._execute(_Job(request=request, initial_limitations=limitations)),
            name=f"chat-run:{run.run_id}",
        )
        self._tasks[run.run_id] = task
        task.add_done_callback(lambda done, run_id=run.run_id: self._task_done(run_id, done))
        return {
            "run_id": run.run_id,
            "conversation_id": conversation_id,
            "requested_mode": mode,
            "effective_mode": effective_mode,
            "events_url": f"/chat/runs/{run.run_id}/events",
        }

    async def _execute(self, job: _Job) -> None:
        request = job.request
        try:
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                async with self._semaphore:
                    if self._cancel_requested(request.run_id):
                        self._finish_cancelled(request.run_id)
                        return
                    if not self.store.transition_run(request.run_id, "running"):
                        return

                    if request.effective_mode == "web":
                        self._stage(request.run_id, "web_search", "正在联网搜索")
                    else:
                        self._stage(request.run_id, "local_retrieval", "正在检索本地资料")
                    # 2026-09-04 占位流式：答案生成前先出字（前端 completed 时用最终 claims 替换）
                    self.store.append_event(request.run_id, "answer.segment", {
                        "segment_id": "__placeholder__",
                        "markdown": "小蜗正在为你整理答案，请稍候…",
                        "claim_ids": [],
                        "placeholder": True,
                    })
                    if request.effective_mode == "web" and not self.settings.web_search_enabled:
                        self._stage(request.run_id, "evidence_check", "正在核验证据")
                        bundle = AnswerBundle(
                            markdown="暂未找到足够可靠的联网证据。联网检索当前未启用。",
                            claims=[{
                                "claim_id": "c1",
                                "text": "暂未找到足够可靠的联网证据。",
                                "kind": "factual",
                                "status": "insufficient",
                                "evidence": [],
                            }],
                            sources=[],
                            limitations=["SearXNG 与 Crawl4AI sidecar 当前未启用。"],
                            terminal_reason="EVIDENCE_INSUFFICIENT",
                        )
                    else:
                        bundle = await asyncio.wait_for(
                            self.runner.run(request),
                            timeout=self.settings.generation_timeout_seconds,
                        )

                    if self._cancel_requested(request.run_id):
                        self._finish_cancelled(request.run_id)
                        return
                    await self._complete(job, bundle)
        except TimeoutError:
            self._fail(request.run_id, "UPSTREAM_TIMEOUT", "回答超过时间预算，请重试。")
        except asyncio.CancelledError:
            self._finish_cancelled(request.run_id)
            raise
        except Exception:
            self._fail(request.run_id, "INTERNAL_ERROR", "处理请求时发生错误，请重试。")

    async def _complete(self, job: _Job, bundle: AnswerBundle) -> None:
        request = job.request
        self._stage(request.run_id, "answering", "正在生成回答")
        # B2: think 决策过程逐条推送(回答之前), 前端折叠卡展示
        for thought in bundle.thoughts:
            self.store.append_event(request.run_id, "thought.step", thought)
        for source in bundle.sources:
            self.store.append_event(
                request.run_id,
                "source.found",
                {
                    key: source.get(key)
                    for key in (
                        "source_id", "title", "display_url", "institution", "domain",
                        "published_at", "fetched_at", "level", "validity", "citation", "tags",
                    )
                },
            )
        segment_id = secrets.token_urlsafe(10)
        self.store.append_event(
            request.run_id,
            "answer.segment",
            {
                "segment_id": segment_id,
                "markdown": bundle.markdown,
                "claim_ids": [claim.get("claim_id") for claim in bundle.claims if claim.get("claim_id")],
            },
        )
        limitations = [*job.initial_limitations, *bundle.limitations]
        answer_id = secrets.token_urlsafe(18)

        history_owner = request.principal.history_owner_key
        if history_owner and request.conversation_id:
            try:
                self.store.append_exchange(
                    conversation_id=request.conversation_id,
                    owner_key=history_owner,
                    run_id=request.run_id,
                    question=request.question,
                    answer=bundle.markdown,
                    metadata={
                        "answer_id": answer_id,
                        "mode": request.requested_mode,
                        "claims": bundle.claims,
                        "sources": bundle.sources,
                        "limitations": limitations,
                    },
                )
            except (KeyError, ValueError):
                limitations.append("本次回答未能写入服务端历史。")

        finished = self.store.finish_run(
            request.run_id,
            "completed",
            "answer.completed",
            {
                "answer_id": answer_id,
                "claims": bundle.claims,
                "sources": bundle.sources,
                "limitations": limitations,
                "terminal_reason": bundle.terminal_reason,
                "truncated": bundle.truncated,
                "stage": "completed",
                "conversation_id": request.conversation_id,
            },
        )
        if finished is not None and bundle.ingestion_candidates and self.ingestion_sink is not None:
            namespace = "demo" if request.principal.auth_mode == "demo" else "production"
            task = asyncio.create_task(
                self._enqueue_candidates(namespace, bundle.ingestion_candidates),
                name=f"review-enqueue:{request.run_id}",
            )
            self._ingestion_tasks.add(task)
            task.add_done_callback(self._ingestion_tasks.discard)

    async def _enqueue_candidates(self, namespace: str, candidates: list[dict[str, Any]]) -> None:
        try:
            await asyncio.to_thread(self.ingestion_sink.enqueue, namespace, candidates)
        except Exception:
            return

    def cancel(self, run_id: str, principal: Principal) -> bool:
        run = self.store.get_run(run_id, principal.session_key)
        if run is None or run.status in {"completed", "cancelled", "failed"}:
            return False
        requested = self.store.request_cancel(run_id, principal.session_key)
        task = self._tasks.get(run_id)
        if requested and task is not None:
            task.cancel()
        return requested

    def cancel_owner(self, principal: Principal) -> None:
        """Cancel in-memory work before demo-owned persistence is reset."""
        self.store.cancel_owner_runs(principal.session_key)
        for run_id, task in tuple(self._tasks.items()):
            run = self.store.get_run(run_id)
            if run is not None and run.owner_key == principal.session_key:
                task.cancel()

    def _task_done(self, run_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(run_id, None)
        if task.cancelled():
            self._finish_cancelled(run_id)

    def _stage(self, run_id: str, stage: str, message: str) -> None:
        self.store.append_event(run_id, "stage.changed", {"stage": stage, "message": message})

    def _cancel_requested(self, run_id: str) -> bool:
        run = self.store.get_run(run_id)
        return run is None or run.cancel_requested

    def _finish_cancelled(self, run_id: str) -> None:
        self.store.finish_run(
            run_id,
            "cancelled",
            "run.cancelled",
            {"stage": "cancelled", "message": "已停止生成。"},
        )

    def _fail(self, run_id: str, code: str, message: str) -> None:
        self.store.finish_run(
            run_id,
            "failed",
            "run.failed",
            {"code": code, "message": message},
            error_code=code,
        )

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._ingestion_tasks:
            await asyncio.gather(*tuple(self._ingestion_tasks), return_exceptions=True)
        close = getattr(self.runner, "close", None)
        if callable(close):
            import inspect

            result = close()
            if inspect.isawaitable(result):
                await result
