"""影子对比：把「纯搜索 + 自研合成」（方案 B）与线上答案（方案 A）并行记录（2026-09-17）。

为什么需要它：评估文档《小蜗_联网架构评估_纯搜索vs智能生成_20260917.md》给出两个相反信号——

- **支持换**：B 端到端 2~4s（A 是 22~43s）；B 的证据是 1000~1500 字逐字原文（A 只给 203 字）；
  A 的答案里甚至出现过**没披露给我们的信息**（无法核验）。
- **反对直接换**：n=1 对比里 B 的输出更简（692 vs 1799 字）且漏掉证据中明确有的信息。

所以先用影子对比把"质量差"量化，再决定。本模块只**记录**，永不参与回答。

设计约束：
1. **绝不影响线上**：`ShadowComparer.compare()` 由后台任务调用，任何异常都被吞掉并记进 `b_error`；
2. **不碰审核队列**：只调检索与合成，不触发进料；
3. **可离线批跑**：`scripts/shadow_run.py` 用它批量对比，不需要走聊天接口（避免给审核队列灌数据）。
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from utils.logger import get_logger

log = get_logger(__name__)

# ── 可核验性代理指标 ────────────────────────────────────────────────────────
# 从答案里抽"可核对的事实"（数字/日期/时刻/金额/电话等），再看它们是否出现在**同一次
# 检索给出的证据**里。这是"答案有没有超出证据"的廉价、可复现的代理指标——
# 不需要 LLM 判官，也不会随模型漂移。
#
# ⚠️ **只覆盖数字/日期类事实**：人名、地名、机构名等（例如实测 A 的答案里出现过、
# 而它给的摘要里没有的"高新区"）它抓不到，那类要靠后续 LLM 判官或人工看原文。
# 不要把它当成"可核验性"的完整度量。
_FACT_RE = re.compile(r"\d[\d:：./\-*×]{1,}\d|\d{3,}")
# 过多噪声的通用数字（年份除外）：单独出现的 1~2 位数字不计
_WS_RE = re.compile(r"\s+")


def evidence_facts(answer: str) -> list[str]:
    """从答案里抽可核对的事实片段（数字类），去重且保持出现顺序。"""
    seen: dict[str, None] = {}
    for match in _FACT_RE.finditer(str(answer or "")):
        token = match.group(0).strip()
        if len(token) >= 2 and token not in seen:
            seen[token] = None
    return list(seen)


def unsupported_facts(answer: str, evidence: str) -> list[str]:
    """答案里出现、但证据里找不到的事实片段（数量越少越可核验）。"""
    haystack = _WS_RE.sub("", str(evidence or ""))
    return [f for f in evidence_facts(answer) if f not in haystack]


# 判官/离线分析要能核对"答案是否超出证据"，所以证据**正文也要存**（不是只存 URL）。
# 每条留 1500 字：与喂给合成的上限一致，够核对又不会把库撑大。
_EVIDENCE_KEEP_CHARS = 1500


def refs_payload(references: list[dict]) -> list[dict]:
    """落库用的证据载荷：标题 + URL + 正文（截断），供判官/离线分析复核。"""
    payload: list[dict] = []
    for ref in references or []:
        if not isinstance(ref, dict):
            continue
        payload.append({
            "title": str(ref.get("title") or "")[:200],
            "url": str(ref.get("url") or "")[:500],
            "content": str(ref.get("content") or "")[:_EVIDENCE_KEEP_CHARS],
        })
    return payload


def refs_evidence(references: list[dict]) -> str:
    """一次检索**披露出来的全部内容**：标题 + URL + 正文。

    URL 也算证据：答案里出现的 `?p=46021` 这类数字确实来自披露的链接，不该算"无依据"。
    """
    parts: list[str] = []
    for ref in references or []:
        parts.append(str(ref.get("title") or ""))
        parts.append(str(ref.get("url") or ""))
        parts.append(str(ref.get("content") or ""))
    return "\n".join(parts)


def injected_context() -> str:
    """我们**主动注入**给自研合成的上下文（当前日期/星期/学期与教学周对照）。

    ⚠️ 这部分不是检索证据，但合成时确实能看到它——所以算 B 的证据时要带上，
    否则答案里合法使用的教学周日期会被误判成"无依据"（实测踩到过）。
    """
    try:
        from agents.qa.nodes import (
            _current_date_text,
            _current_weekday_text,
            _semester_context_text,
        )

        return "\n".join([_current_date_text(), _current_weekday_text(), _semester_context_text()])
    except Exception:  # noqa: BLE001
        return ""


def verifiable_ratio(answer: str, evidence: str) -> float | None:
    """可核验比例 = 1 - 未命中事实 / 全部事实；无事实可核对时返回 None。"""
    facts = evidence_facts(answer)
    if not facts:
        return None
    missing = unsupported_facts(answer, evidence)
    return round(1 - len(missing) / len(facts), 3)


# ── 记录库 ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_runs (
    run_id            TEXT PRIMARY KEY,
    namespace         TEXT NOT NULL,
    source            TEXT NOT NULL,
    question          TEXT NOT NULL,
    created_at        REAL NOT NULL,

    a_answer          TEXT,
    a_latency         REAL,
    a_ref_chars       INTEGER,
    a_ref_count       INTEGER,
    a_verifiable      REAL,
    a_unsupported     TEXT,
    a_refs            TEXT,
    a_limitations     TEXT,

    b_answer          TEXT,
    b_search_latency  REAL,
    b_compose_latency REAL,
    b_ref_chars       INTEGER,
    b_ref_count       INTEGER,
    b_verifiable      REAL,
    b_unsupported     TEXT,
    b_refs            TEXT,
    b_error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_shadow_runs_created ON shadow_runs(created_at);
"""


class ShadowStore:
    """影子对比结果库（独立 sqlite，与审核库/会话库隔离）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def record(self, row: dict[str, Any]) -> str:
        run_id = str(row.get("run_id") or "shadow-" + secrets.token_urlsafe(12))
        payload = {
            "run_id": run_id,
            "namespace": str(row.get("namespace") or "demo"),
            "source": str(row.get("source") or "production"),
            "question": str(row.get("question") or "")[:500],
            "created_at": float(row.get("created_at") or time.time()),
            "a_answer": row.get("a_answer"),
            "a_latency": row.get("a_latency"),
            "a_ref_chars": row.get("a_ref_chars"),
            "a_ref_count": row.get("a_ref_count"),
            "a_verifiable": row.get("a_verifiable"),
            "a_unsupported": json.dumps(row.get("a_unsupported") or [], ensure_ascii=False),
            "a_refs": json.dumps(row.get("a_refs") or [], ensure_ascii=False),
            "a_limitations": json.dumps(row.get("a_limitations") or [], ensure_ascii=False),
            "b_answer": row.get("b_answer"),
            "b_search_latency": row.get("b_search_latency"),
            "b_compose_latency": row.get("b_compose_latency"),
            "b_ref_chars": row.get("b_ref_chars"),
            "b_ref_count": row.get("b_ref_count"),
            "b_verifiable": row.get("b_verifiable"),
            "b_unsupported": json.dumps(row.get("b_unsupported") or [], ensure_ascii=False),
            "b_refs": json.dumps(row.get("b_refs") or [], ensure_ascii=False),
            "b_error": row.get("b_error"),
        }
        columns = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                f"INSERT OR REPLACE INTO shadow_runs({columns}) VALUES ({marks})",
                tuple(payload.values()),
            )
            conn.commit()
        return run_id

    def rows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM shadow_runs ORDER BY created_at DESC LIMIT ?", (int(limit),)
            )
            return [dict(row) for row in cur.fetchall()]


# ── 对比器 ─────────────────────────────────────────────────────────────────

# 方案 B 给合成的证据上限：**这是关键差异**——本地知识库路径的
# `_build_candidates_summary` 每条只给 120 字，联网场景必须放开，否则纯搜索的
# 1000+ 字证据等于白拿。
_WEB_CANDIDATE_CHARS = 1500


def build_web_candidates_summary(references: list[dict], *, limit: int = 8) -> str:
    """把联网 references 拼成候选摘要（每条最多 1500 字），供自研合成使用。"""
    lines: list[str] = []
    for index, ref in enumerate(references[:limit], start=1):
        title = str(ref.get("title") or "未命名").strip()[:120]
        url = str(ref.get("url") or "").strip()
        date = str(ref.get("date") or "").strip()
        content = str(ref.get("content") or "").strip()[:_WEB_CANDIDATE_CHARS]
        head = f"[{index}] 《{title}》"
        if date:
            head += f" 日期:{date}"
        if url:
            head += f" 来源:{url}"
        lines.append(f"{head}: {content}")
    return "\n".join(lines) if lines else "（无候选片段）"


class ShadowComparer:
    """跑方案 B 并把 A/B 一起记下来。**只记录，不参与回答。**"""

    def __init__(
        self,
        store: ShadowStore,
        search: Any,
        *,
        compose: Callable[[str, list[dict]], str] | None = None,
        limit: int = 8,
    ) -> None:
        self.store = store
        self.search = search
        self._compose = compose  # 测试注入；默认走 compose_with_own_llm
        self.limit = limit

    # ---- 方案 B 的两段 ----
    async def retrieve(self, question: str) -> tuple[list[dict], float]:
        """纯搜索取证据（不传 model）。查询词用检索关键词，不用整段提示词。"""
        from xiaowo_web.evidence.rewrite import campus_search_keywords

        keywords = campus_search_keywords(question)
        query = " ".join(keywords) if keywords else question
        started = time.time()
        refs = await self.search.references_only(query, limit=self.limit)
        return [r for r in refs if isinstance(r, dict)], time.time() - started

    def compose(self, question: str, references: list[dict]) -> tuple[str, float]:
        if self._compose is not None:
            started = time.time()
            return self._compose(question, references), time.time() - started
        return compose_with_own_llm(question, references)

    # ---- 主入口 ----
    async def compare(
        self,
        *,
        question: str,
        a_answer: str,
        a_references: list[dict],
        a_latency: float | None = None,
        a_limitations: list[str] | None = None,
        namespace: str = "demo",
        source: str = "production",
    ) -> str:
        """记录一次 A/B 对比。**任何失败都只记 b_error，绝不抛出。**"""
        row: dict[str, Any] = {
            "question": question,
            "namespace": namespace,
            "source": source,
            "a_answer": a_answer,
            "a_latency": a_latency,
            "a_ref_count": len(a_references or []),
            "a_ref_chars": sum(len(str(r.get("content") or "")) for r in (a_references or [])),
            "a_refs": refs_payload(a_references),
            "a_limitations": list(a_limitations or []),
        }
        a_evidence = refs_evidence(a_references)
        row["a_verifiable"] = verifiable_ratio(a_answer, a_evidence)
        row["a_unsupported"] = unsupported_facts(a_answer, a_evidence)

        try:
            refs, search_latency = await self.retrieve(question)
            answer, compose_latency = await asyncio.to_thread(self.compose, question, refs)
            # B 的证据 = 检索披露内容 + 我们注入的日期/学期上下文（两者合成都看得到）
            b_evidence = refs_evidence(refs) + "\n" + injected_context()
            row.update(
                b_answer=answer,
                b_search_latency=round(search_latency, 2),
                b_compose_latency=round(compose_latency, 2),
                b_ref_count=len(refs),
                b_ref_chars=sum(len(str(r.get("content") or "")) for r in refs),
                b_refs=refs_payload(refs),
                b_verifiable=verifiable_ratio(answer, b_evidence),
                b_unsupported=unsupported_facts(answer, b_evidence),
            )
        except Exception as exc:  # noqa: BLE001 —— 影子链路失败绝不能影响线上
            log.warning(f"影子对比方案B失败: {type(exc).__name__}: {exc}")
            row["b_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        try:
            return self.store.record(row)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"影子对比记录失败: {exc}")
            return ""


def compose_with_own_llm(question: str, references: list[dict]) -> tuple[str, float]:
    """用**我们自己的** COMPOSE_PROMPT 与 LLM 合成（方案 B 的第二段）。

    与本地知识库路径的区别：证据每条给到 1500 字（本地路径是 120 字），这正是换成
    纯搜索后能拿到更多证据的意义所在。

    Returns:
        (答案正文, 耗时秒)。失败抛异常，由 `ShadowComparer` 记为 `b_error`。
    """
    started = time.time()
    # 延迟导入：避免 xiaowo_web ←→ agents 的循环依赖
    from agents.qa.nodes import (
        COMPOSE_PROMPT,
        _current_date_text,
        _current_weekday_text,
        _semester_context_text,
    )
    from langchain_core.prompts import ChatPromptTemplate
    from utils.llm_client import create_llm

    candidates_summary = build_web_candidates_summary(references, limit=8)
    # 注：injected_context() 里那三个字符串与下面 invoke_vars 里用的是同一份实现
    llm = create_llm(temperature=0.3)
    prompt = ChatPromptTemplate.from_messages([
        ("system", COMPOSE_PROMPT),
        ("human", "请直接输出回答正文，第一句必须是面向用户的内容。"),
    ])
    chain = prompt | llm
    result = chain.invoke({
        "query": question,
        "current_date": _current_date_text(),
        "current_weekday": _current_weekday_text(),
        "semester_context": _semester_context_text(),
        "intent": "知识问答",
        "chat_history": "（无）",
        "student_info": "（未登录）",
        "candidates_summary": candidates_summary,
        "tool_summary": "（无）",
        "candidates_found": "已达到匹配阈值",
        "structured_note": "本回答无结构化数据卡，请按上述规则生成正文。",
    })
    return str(getattr(result, "content", result)), time.time() - started
