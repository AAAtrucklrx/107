"""Adapters between the async Web contract and the existing synchronous QA graph."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.evidence.trust import SourceTrustStore


_URL_PATTERN = re.compile(r"https?://[^\s<>\]】)）]+", re.IGNORECASE)
_INLINE_CITATION = re.compile(r"\[\d+\]")
# 时效词（2026-09-04：此类问题的回答会过期 → 不入语义缓存）
_TIME_SENSITIVE = re.compile(r"(?:最新|最近|近期|近日|最近几天|今天|现在|当前|截至|刚刚|本周|本月|今年|目前|现行|还有效吗)")

# ── 闲聊入口快路径（2026-09-03：短问候句走模板，跳过知识库检索与整个 QA 图） ──
_CHITCHAT_WORDS_CACHE: tuple[str, ...] | None = None
_CHITCHAT_REPLY = "你好呀！我是小蜗，科大校园智能助手，有什么可以帮你？"


def _chitchat_words() -> tuple[str, ...]:
    global _CHITCHAT_WORDS_CACHE
    if _CHITCHAT_WORDS_CACHE is None:
        try:
            from agents.qa.nodes import _CHITCHAT_WORDS as words
            _CHITCHAT_WORDS_CACHE = tuple(words)
        except Exception:
            _CHITCHAT_WORDS_CACHE = (
                "你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗",
                "谢谢", "感谢", "辛苦", "你是谁", "拜拜", "再见",
            )
    return _CHITCHAT_WORDS_CACHE


def is_chitchat_query(question: str) -> bool:
    """闲聊判定：仅当问题短（≤12 字）且含问候特征词（避免误判真实问题）。"""
    q = (question or "").strip().lower()
    if not q or len(q) > 12:
        return False
    return any(word in q for word in _chitchat_words())


def chitchat_reply() -> AnswerBundle:
    """闲聊模板回答（不依赖 LLM/检索，毫秒级）。"""
    return AnswerBundle(
        markdown=_CHITCHAT_REPLY,
        claims=[{
            "claim_id": "c1", "text": _CHITCHAT_REPLY, "kind": "chitchat",
            "status": "confirmed", "evidence": [],
        }],
        sources=[], limitations=[], terminal_reason="local_answer",
    )
_TOOL_TITLES = {
    "query_schedule": "个人课表",
    "query_daily_schedule": "个人日课表",
    "find_empty_room": "空教室查询",
    "query_grade": "个人成绩",
    "calc_gpa": "个人绩点",
    "query_exam": "考试安排",
    "search_courses": "课程目录",
    "query_course_selection": "个人选课记录",
    "query_program": "培养方案",
    "get_my_program": "个人培养方案",
    "get_program_progress": "培养进度",
    "plan_semester": "学期规划",
    "recommend_courses": "课程推荐",
    "compare_courses": "课程对比",
    "analyze_teacher": "教师评价",
    "check_course_conflict": "独立课表冲突检查",
    "evaluate_selection_pressure": "退补选压力评估",
    "render_link": "校园官方入口",
    "query_activities": "青春科大公开活动",
    "search_faq": "小蜗校园知识库",
}
_RESULT_COLLECTION_KEYS = frozenset({
    "activities", "conflicts", "courses", "data", "events", "exams", "grades",
    "items", "recommendations", "results", "schedule", "semesters",
})
_RESULT_METADATA_KEYS = frozenset({
    "count", "error", "limitations", "message", "source", "status", "success",
    "top_score", "found",
})


_PERSONAL_TOOLS = frozenset({
    "query_schedule", "query_daily_schedule", "query_grade", "calc_gpa", "query_exam",
    "query_course_selection", "get_my_program", "get_program_progress", "plan_semester",
    "import_schedule",
})


def _used_personal_tools(tool_results: list[dict]) -> bool:
    """本轮回答是否消费了个人数据工具结果——个人化回答禁止写语义缓存。"""
    for r in tool_results or []:
        if isinstance(r, dict) and str(r.get("tool") or "") in _PERSONAL_TOOLS and r.get("status") == "done":
            return True
    return False


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _public_url(value: Any) -> str | None:
    match = _URL_PATTERN.search(str(value or ""))
    if match is None:
        return None
    raw = match.group(0).rstrip(".,;:!?，。；：！？'\"")
    parts = urlsplit(raw)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return urlunsplit((parts.scheme.casefold(), parts.netloc, parts.path or "/", parts.query, ""))


def _score(value: Any) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _tool_result_is_usable(entry: dict[str, Any]) -> bool:
    if entry.get("status") != "done" or not isinstance(entry.get("result"), dict):
        return False
    result = entry["result"]
    if result.get("error") or result.get("success") is False or result.get("found") is False:
        return False
    present_collections = [key for key in _RESULT_COLLECTION_KEYS if key in result]
    if present_collections:
        if any(bool(result.get(key)) for key in present_collections):
            return True
        if result.get("found") is True:
            return True
        try:
            if float(result.get("count") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        return False
    meaningful = {
        key: value
        for key, value in result.items()
        if key not in _RESULT_METADATA_KEYS and value not in (None, "", [], {})
    }
    return bool(meaningful) or result.get("found") is True


def _candidate_records(
    candidates: list[dict[str, Any]],
    trust_store: SourceTrustStore,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    best_by_origin: dict[str, dict[str, Any]] = {}
    for candidate in sorted(candidates, key=lambda item: -_score(item.get("score"))):
        url = _public_url(candidate.get("display_url") or candidate.get("source"))
        identity = url or str(candidate.get("id") or candidate.get("chunk_id") or _digest(candidate))
        best_by_origin.setdefault(identity, candidate)

    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for identity, candidate in list(best_by_origin.items())[:5]:
        url = identity if identity.startswith(("http://", "https://")) else None
        decision = trust_store.classify_url_without_dns(url) if url else None
        title = str(candidate.get("title") or candidate.get("subcategory") or "本地知识片段").strip()
        relevance = _score(candidate.get("score"))
        source_id = "local-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        institution = str(candidate.get("institution") or (decision.institution if decision else "") or "小蜗本地知识库")
        level = str(candidate.get("source_level") or candidate.get("level") or (decision.level if decision else "local_curated"))
        tags = list(candidate.get("tags") or ())
        if candidate.get("namespace") == "demo" and "demo" not in tags:
            tags.append("demo")
        source = {
            "source_id": source_id,
            "title": title,
            "display_url": url,
            "institution": institution,
            "domain": (urlsplit(url).hostname or "") if url else None,
            "published_at": candidate.get("published_at") or candidate.get("last_updated") or None,
            "fetched_at": candidate.get("fetched_at") or None,
            "level": level,
            "validity": str(candidate.get("validity") or "active"),
            "tags": tags,
            "relevance_score": relevance,
        }
        evidence = {
            "source_id": source_id,
            "evidence_type": "local",
            "relation": "supports",
            "excerpt_hash": _digest(str(candidate.get("content") or "")),
            "relevance_score": relevance,
        }
        records.append((source, evidence))
    return records


def _tool_records(tool_results: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for entry in tool_results:
        if not _tool_result_is_usable(entry):
            continue
        result = entry["result"]
        tool_name = str(entry.get("tool") or "campus_tool")
        url = _public_url(
            result.get("url")
            or result.get("official_url")
            or result.get("source_url")
            or ""
        )
        title = str(result.get("name") or result.get("title") or _TOOL_TITLES.get(tool_name) or tool_name)
        identity = url or f"tool:{tool_name}:{title}"
        if identity in seen:
            continue
        seen.add(identity)
        source_label = str(result.get("source") or "").strip()
        cached = source_label.casefold() in {"fallback", "cache", "cached"} or "缓存" in source_label
        institution = (
            source_label
            if source_label and source_label.casefold() not in {"real", "fallback", "cache", "cached"}
            else ("小蜗本地缓存" if cached else "小蜗校园数据工具")
        )
        source_id = "tool-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        source = {
            "source_id": source_id,
            "title": title,
            "display_url": url,
            "institution": institution,
            "domain": (urlsplit(url).hostname or "") if url else None,
            "published_at": result.get("published_at") or None,
            "fetched_at": result.get("fetched_at") or None,
            "level": "tool_cache" if cached else "tool_result",
            "validity": "cached" if cached else "active",
            "tags": ["personal"] if tool_name not in {"render_link", "query_activities", "search_faq"} else [],
        }
        evidence = {
            "source_id": source_id,
            "evidence_type": "tool",
            "relation": "supports",
            "excerpt_hash": _digest(result),
        }
        records.append((source, evidence))
    return records


def _with_citations(
    records: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for citation, (source, relation) in enumerate(records, start=1):
        sources.append({**source, "citation": citation})
        evidence.append({**relation, "citation": citation})
    return sources, evidence


class QaRunner(Protocol):
    async def run(self, request: QaRunRequest) -> AnswerBundle: ...


class ApprovedRetriever(Protocol):
    def search(self, question: str, principal: Any) -> dict[str, Any]: ...


class LegacyQaRunner:
    """Run the existing LangGraph entrypoint in a bounded executor."""

    def __init__(
        self,
        run_qa_func: Callable[..., dict[str, Any]] | None = None,
        *,
        max_workers: int = 4,
        approved_retriever: ApprovedRetriever | None = None,
        semantic_cache: Any | None = None,
    ) -> None:
        self._run_qa_func = run_qa_func
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="xiaowo-qa")
        self._trust_store = SourceTrustStore()
        self._approved_retriever = approved_retriever
        # 语义缓存（可选）：None = 禁用（测试默认）；生产由 main 注入共享单例
        self._semantic_cache = semantic_cache
        # ①: reranker/embedder 后台预热(避免首次问答卡顿 3s/20s; 缺失时静默跳过)
        try:
            from knowledge.reranker import prewarm
            prewarm()
        except Exception:
            pass
        try:
            from knowledge.vector_store import prewarm_embedder
            prewarm_embedder()
        except Exception:
            pass

    def _resolve_runner(self) -> Callable[..., dict[str, Any]]:
        if self._run_qa_func is None:
            from agents.qa.graph import run_qa

            self._run_qa_func = run_qa
        return self._run_qa_func

    async def run(self, request: QaRunRequest) -> AnswerBundle:
        # 闲聊入口快路径：问候/道谢等短句直接模板回应，跳过批准索引检索与 QA 图
        if is_chitchat_query(request.question):
            return chitchat_reply()
        # 语义缓存查询：公共知识问题命中时直接返回历史答案（毫秒级）。
        # 个人化回答不写缓存（见下方写入条件），但读缓存对全员开放——
        # 公共问题的答案与提问者身份无关。
        namespace = "demo" if request.principal.auth_mode == "demo" else "production"
        cached = None
        if self._semantic_cache is not None:
            try:
                cached = self._semantic_cache.lookup(request.question, namespace)
            except Exception:
                cached = None  # 缓存层任何异常都旁路，不影响主链路
        if cached is not None:
            if request.emit_stage is not None:
                request.emit_stage("缓存命中", f"⚡ 语义缓存命中（相似度 {cached['score']}）")
            return AnswerBundle(
                markdown=cached["answer"],
                claims=[{
                    "claim_id": "c1", "text": cached["answer"], "kind": "factual",
                    "status": "confirmed",
                    "evidence": [{"source_id": "semantic-cache", "relation": "supports",
                                  "quote": f"语义缓存命中（相似度 {cached['score']}）"}],
                }],
                sources=[{
                    "citation": "缓存", "source_id": "semantic-cache",
                    "title": "语义缓存回答", "source": "本地语义缓存",
                }],
                structured=list(cached.get("structured") or []),
                limitations=[],
                terminal_reason="cache_hit",
                thoughts=[{"round": 0, "decision": "cache_hit",
                           "reason": f"语义缓存命中（相似度 {cached['score']}），跳过全链路"}],
            )
        runner = self._resolve_runner()
        profile = dict(request.principal.profile) if request.principal.is_authenticated else {}
        student_id = request.principal.principal_id if request.principal.is_authenticated else None
        approved_candidates: list[dict[str, Any]] = []
        approved_found = False
        approved_limitations: list[str] = []
        if self._approved_retriever is not None:
            try:
                approved = await asyncio.to_thread(
                    self._approved_retriever.search,
                    request.question,
                    request.principal,
                )
                approved_candidates = [
                    item for item in (approved.get("results") or []) if isinstance(item, dict)
                ]
                approved_found = bool(approved.get("found"))
                approved_limitations = [
                    str(value) for value in (approved.get("limitations") or []) if str(value).strip()
                ]
            except Exception:
                approved_limitations.append("已批准知识索引暂不可用，本轮已回退既有本地知识库。")

        call_args: dict[str, Any] = {
            "module_signal": "智能问答",
            "student_id": student_id,
            "user_profile": profile,
            "chat_history": request.chat_history,
        }
        try:
            parameters = inspect.signature(runner).parameters.values()
            accepts_kwargs = any(value.kind is inspect.Parameter.VAR_KEYWORD for value in parameters)
            parameter_names = {value.name for value in parameters}
        except (TypeError, ValueError):
            accepts_kwargs = False
            parameter_names = set()
        if accepts_kwargs or "supplemental_candidates" in parameter_names:
            call_args["supplemental_candidates"] = approved_candidates
        if accepts_kwargs or "supplemental_candidates_found" in parameter_names:
            call_args["supplemental_candidates_found"] = approved_found
        # A 方案动作播报：agent 决策/工具动作经 emit_stage("action", msg) 实时推给前端
        if (
            (accepts_kwargs or "action_sink" in parameter_names)
            and request.emit_stage is not None
        ):
            def _action_sink(message: str, payload: dict | None = None) -> None:
                if message == "answer_delta":
                    # compose 增量流式：正文 token 推 answer.delta 事件，不走 stage/table 通道
                    if payload and request.emit_delta is not None:
                        request.emit_delta(str(payload.get("delta") or ""))
                    return
                request.emit_stage("action", message)
                if payload and request.emit_table is not None:
                    request.emit_table(payload)

            call_args["action_sink"] = _action_sink
        call = partial(runner, request.question, **call_args)
        loop = asyncio.get_running_loop()
        result = dict(await loop.run_in_executor(self._executor, call))
        merged_candidates: list[dict[str, Any]] = []
        seen_candidates: set[str] = set()
        for candidate in [
            *(value for value in (result.get("candidates") or []) if isinstance(value, dict)),
            *approved_candidates,
        ]:
            identity = str(
                candidate.get("id")
                or candidate.get("chunk_id")
                or f"{candidate.get('source')}:{_digest(candidate.get('content') or '')}"
            )
            if identity in seen_candidates:
                continue
            seen_candidates.add(identity)
            merged_candidates.append(candidate)
        result["candidates"] = merged_candidates
        result["candidates_found"] = bool(result.get("candidates_found") or approved_found)
        answer = str(result.get("answer") or result.get("clarify_question") or "").strip()
        if not answer:
            answer = "暂时没有形成可用回答。"

        tool_results = [value for value in (result.get("tool_results") or []) if isinstance(value, dict)]
        candidates = [value for value in (result.get("candidates") or []) if isinstance(value, dict)]
        # B2: think 决策过程透出(前端折叠卡); 只透 decision/reason, 不露提示词与工具原始数据
        thoughts: list[dict[str, Any]] = []
        for entry in (result.get("thought_log") or []):
            if not isinstance(entry, dict):
                continue
            thoughts.append({
                "round": entry.get("round") or 0,
                "decision": str(entry.get("decision") or "compose"),
                "reason": str(entry.get("reason") or "").strip(),
            })
        # ③: 检索过程记录(首轮召回/子查询重查)并入思考链, 按 round 排序
        for entry in (result.get("retrieval_log") or []):
            if not isinstance(entry, dict):
                continue
            thoughts.append({
                "round": entry.get("round") or 0,
                "decision": str(entry.get("decision") or "retrieve"),
                "reason": str(entry.get("reason") or "").strip(),
            })
        thoughts.sort(key=lambda item: (item["round"], len(str(item["reason"]))))
        tool_records = _tool_records(tool_results)
        candidate_records = _candidate_records(candidates, self._trust_store)
        candidate_supports = candidate_records if result.get("candidates_found") else []
        sources, evidence = _with_citations(tool_records + candidate_records)
        supporting_ids = {
            relation["source_id"]
            for _, relation in (tool_records + candidate_supports)
        }
        evidence = [
            ({**relation, "relation": "supports"} if relation["source_id"] in supporting_ids else {**relation, "relation": "context"})
            for relation in evidence
        ]
        intent = str(result.get("intent") or "")
        if intent in {"闲聊", "chitchat"}:
            claim_kind = "chitchat"
        elif "推荐" in intent:
            claim_kind = "recommendation"
        else:
            claim_kind = "factual"
        # 2026-09-04：链路降级（LLM 不可用/返回空等 error）不算确认，
        # 防止降级内容被当作证据并写入语义缓存
        claim_status = (
            "confirmed"
            if (supporting_ids or claim_kind == "chitchat") and not result.get("error")
            else "insufficient"
        )
        claim = {
            "claim_id": "c1",
            "text": answer,
            "kind": claim_kind,
            "status": claim_status,
            "evidence": evidence if claim_kind != "chitchat" else [],
        }
        limitations: list[str] = []
        if result.get("error"):
            limitations.append("本地问答链路发生降级，回答可能不完整。")
        limitations.extend(value for value in approved_limitations if value not in limitations)
        if claim_status == "insufficient" and claim["kind"] == "factual":
            limitations.append("当前本地结果未暴露足够的结构化证据元数据。")
        if claim_status == "confirmed" and not _INLINE_CITATION.search(answer):
            citations = "".join(f"[{source['citation']}]" for source in sources[:3])
            answer = f"{answer.rstrip()} {citations}".rstrip()
            claim["text"] = answer
        # 语义缓存写入：仅公共知识回答（确认有支撑 + 未调用个人数据工具），
        # 个人化回答不缓存，避免跨会话/跨用户复用个人数据
        if (
            self._semantic_cache is not None
            and claim_status == "confirmed"
            and not result.get("error")
            and not _TIME_SENSITIVE.search(request.question)
            and not _used_personal_tools(tool_results)
        ):
            try:
                source_hashes = sorted({_digest(c.get("content") or "") for c in candidates if c.get("content")})
                self._semantic_cache.store(
                    request.question, answer, namespace,
                    source_hashes=source_hashes,
                    structured=list(result.get("structured") or []),
                )
            except Exception:
                pass  # 缓存写入失败不影响回答
        return AnswerBundle(
            markdown=answer,
            claims=[claim],
            sources=sources,
            limitations=limitations,
            structured=list(result.get("structured") or []),
            terminal_reason="local_answer",
            thoughts=thoughts,
            truncated=bool(result.get("truncated")),
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
