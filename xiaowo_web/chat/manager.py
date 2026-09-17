"""Chat run ownership, deadlines, persistence, and terminal-state orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from xiaowo_web.auth.models import Principal
from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.chat.privacy import is_personal_query
from xiaowo_web.chat.runner import QaRunner
from xiaowo_web.errors import ApiError
from xiaowo_web.settings import WebSettings
from xiaowo_web.storage import WebStore

from utils.logger import get_logger


log = get_logger(__name__)


def _table_key(table: dict[str, Any]) -> str:
    """结构化卡的**内容指纹**：内容相同即视为同一张卡（用于抑制重复推送）。

    刻意不用 `title|source_tool|行数` 这类弱键——不同内容但同形的卡会被误判为重复。
    """
    try:
        blob = json.dumps(table, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(table)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Job:
    request: QaRunRequest
    initial_limitations: list[str]
    # compose 增量流式正文累积（超时部分收尾用；emit_delta 闭包写入）
    streamed: dict[str, str] = field(default_factory=dict)


class ChatManager:
    def __init__(
        self,
        settings: WebSettings,
        store: WebStore,
        runner: QaRunner,
        ingestion_sink: Any | None = None,
        page_fetcher: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.runner = runner
        self.ingestion_sink = ingestion_sink
        # 联网引用 → 整页候选 的抓取器（EvidencePipeline.fetch_candidates_for_ingestion）
        self.page_fetcher = page_fetcher
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._ingestion_tasks: set[asyncio.Task[None]] = set()
        # 每个 run 已实时推送过的结构化卡内容指纹（run 结束重推时据此跳过，避免同卡两条事件）
        self._emitted_table_keys: dict[str, set[str]] = {}

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
        streamed: dict[str, str] = {"text": ""}
        emitted_keys = self._emitted_table_keys.setdefault(run.run_id, set())

        def _emit_table(table: dict) -> None:
            # 记录内容指纹后实时推卡；run 结束时的重推会跳过已推过的卡
            emitted_keys.add(_table_key(table))
            self.store.append_event(run.run_id, "data.table", table)

        def _emit_delta(delta: str) -> None:
            streamed["text"] += delta
            self.store.append_event(run.run_id, "answer.delta", {"delta": delta})

        request = QaRunRequest(
            run_id=run.run_id,
            question=question,
            requested_mode=mode,
            effective_mode=effective_mode,
            principal=principal,
            conversation_id=conversation_id,
            chat_history=history,
            emit_stage=lambda stage, message: self._stage(run.run_id, stage, message),
            emit_table=_emit_table,
            emit_delta=_emit_delta,
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
            self._execute(_Job(request=request, initial_limitations=limitations, streamed=streamed)),
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
                            limitations=["联网检索当前未启用。"],
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
            if (job.streamed.get("text") or "").strip():
                # 2026-09-05 流式部分收尾：正文已在逐段推出时，超时不再整体失败，
                # 以已生成内容收尾（truncated），前端保留已流出的文本
                log.warning(
                    "chat run %s 生成超时，已有 %d 字流式正文，部分收尾",
                    request.run_id, len(job.streamed["text"]),
                )
                self._stage(request.run_id, "answering", "生成超时，展示已生成内容")
                finished = self.store.finish_run(
                    request.run_id,
                    "completed",
                    "answer.completed",
                    {
                        "answer_id": secrets.token_urlsafe(18),
                        "claims": [],
                        "sources": [],
                        "limitations": ["生成超时，以上为已生成的部分内容；重新提问可获取完整回答。"],
                        "terminal_reason": "GENERATION_TIMEOUT_PARTIAL",
                        "truncated": True,
                        "stage": "completed",
                        "conversation_id": request.conversation_id,
                    },
                )
                if finished is None:
                    self._fail(request.run_id, "UPSTREAM_TIMEOUT", "回答超过时间预算，请重试。")
            else:
                self._fail(request.run_id, "UPSTREAM_TIMEOUT", "回答超过时间预算，请重试。")
        except asyncio.CancelledError:
            self._finish_cancelled(request.run_id)
            raise
        except Exception:
            log.exception("chat run %s 内部错误", request.run_id)
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
        # 阶段1 结构化数据卡：工具表格以独立事件先于正文推流（前端先渲染卡片）。
        # act 节点已实时推过的卡在此跳过——旧实现两处都推，导致同一张卡发两条事件
        # （前端虽按 key 覆盖不可见，但浪费 SSE 带宽并在 web_chat_events 里多存一行）。
        emitted_keys = self._emitted_table_keys.get(request.run_id) or set()
        for table in list(getattr(bundle, "structured", None) or []):
            if _table_key(table) in emitted_keys:
                continue
            self.store.append_event(request.run_id, "data.table", table)
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

        # 联网引用进料（2026-09-16）：smart 只给片段，交给后台抓整页再入审核库
        ref_urls = [str(u).strip() for u in (getattr(bundle, "ingestion_urls", None) or []) if str(u).strip()]
        if (
            finished is not None
            and ref_urls
            and self.ingestion_sink is not None
            and self.page_fetcher is not None
        ):
            namespace = "demo" if request.principal.auth_mode == "demo" else "production"
            task = asyncio.create_task(
                self._ingest_references(
                    namespace, ref_urls, getattr(bundle, "ingestion_snippets", None)
                ),
                name=f"review-ingest-refs:{request.run_id}",
            )
            self._ingestion_tasks.add(task)
            task.add_done_callback(self._ingestion_tasks.discard)

    async def _ingest_references(
        self, namespace: str, urls: list[str],
        snippets: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """后台把联网引用的页面抓成整页后入审核库（失败不影响已返回的回答）。

        `snippets` 是检索器随 references 返回的摘要，供 robots 禁抓站点兜底
        （抓不到正文时改用摘要，并由下游标注「仅搜索摘要」）。
        """
        try:
            candidates = await self.page_fetcher(urls, snippets=snippets)
            if candidates:
                await asyncio.to_thread(self.ingestion_sink.enqueue, namespace, candidates)
                snippet_only = sum(
                    1 for item in candidates
                    if str(item.get("content_type") or "") == "text/search-snippet"
                )
                log.info(
                    f"联网引用入审核库: 抓成 {len(candidates)}/{len(urls)} 条"
                    f"（其中仅摘要 {snippet_only} 条）(namespace={namespace})"
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"联网引用入审核库失败: {exc}")

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
        self._emitted_table_keys.pop(run_id, None)  # 去重账本随 run 结束释放，避免泄漏
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
