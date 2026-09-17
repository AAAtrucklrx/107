"""影子对比：把「纯搜索 + 自研合成」（方案 B）与线上答案（方案 A）并行记录（2026-09-17）。

为什么需要它：评估文档给出两个相反信号——

- **支持换**：B 端到端 2~4s（A 是 22~43s）；B 的证据是 1000~1500 字逐字原文（A 只给 203 字）。
- **反对直接换**：部分题目 B 的输出更简、且在**垃圾证据**上会硬编（判官实测抓到）。

所以先用影子对比把"质量差"量化，再决定。本模块只**记录**，永不参与回答。

设计约束：
1. **绝不影响线上**：`ShadowComparer.compare()` 由后台任务调用，任何异常都被吞掉并记进 `b_error`；
2. **不碰审核队列**：只调检索与合成，不触发进料；
3. **可离线批跑**：`scripts/shadow_run.py` 用它批量对比，不走聊天接口（避免给审核队列灌数据）。

⚠️ 合成/指标/闸门的**唯一实现**在 `evidence/compose.py`（生产路径也用那份），本模块只
import 复用，避免"影子版"和"生产版"漂移。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from utils.logger import get_logger

from xiaowo_web.evidence.compose import (  # noqa: F401  (对外保持同名，测试/脚本直接引用)
    build_web_candidates_summary,
    compose_with_own_llm,
    evidence_facts,
    injected_context,
    refs_evidence,
    unsupported_facts,
    verifiable_ratio,
)

log = get_logger(__name__)

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

    async def retrieve(self, question: str) -> tuple[list[dict], float]:
        """纯搜索取证据（不传 model）。查询词用检索关键词，不用整段提示词。"""
        from xiaowo_web.evidence.rewrite import campus_search_keywords

        keywords = campus_search_keywords(question)
        query = " ".join(keywords) if keywords else question
        started = time.time()
        refs = await self.search.references_only(query, limit=self.limit)
        return [r for r in refs if isinstance(r, dict)], time.time() - started

    def compose(self, question: str, references: list[dict]) -> tuple[str, float]:
        started = time.time()
        if self._compose is not None:
            return self._compose(question, references), time.time() - started
        return compose_with_own_llm(question, references), time.time() - started

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
