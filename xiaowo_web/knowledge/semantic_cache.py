"""语义缓存：公共知识问答的「问题→答案」加速层（v1）。

- 命中：query embedding 与缓存条目余弦相似度 ≥ 阈值（默认 0.92，宁缺勿滥）且未过期
- 失效：知识发布激活时按 chunk content_hash 对比定向清理——答案引用的 chunk
  hash 不在新发布集合中（依据已变化）则该条目失效；未受影响的缓存保留
- 存储：独立 SQLite（与 review.db 同目录），条目量小（数百级），逐条余弦足矣
- 来源：**连同原始 sources 一起存**（2026-09-17 修 P1-3），命中时还原 —— 否则缓存回答
  只剩一条「语义缓存回答」占位来源，用户无从核对、反馈分诊也拿不到 URL
- 隔离：demo / production 命名空间分开；个人数据工具参与的回答一律不写缓存
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD = 0.92
DEFAULT_TTL_SECONDS = 86400.0  # 24h：校园知识时效性强，宁可短
# 联网条目单独一套短 TTL（2026-09-18）：联网内容没有 chunk hash 可做定向失效，
# 来源里还常有未核实公众号/自媒体，不能用本地那套 24h 来管。
DEFAULT_WEB_TTL_SECONDS = 1800.0  # 30 分钟

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    embedding TEXT NOT NULL,
    source_hashes TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    kind TEXT NOT NULL DEFAULT 'local',
    limitations TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS semantic_cache_structured(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_id INTEGER NOT NULL,
    structured TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_cache_ns
    ON semantic_cache(namespace, created_at);
"""

def _ensure_columns(conn: "sqlite3.Connection") -> None:
    """轻量迁移：旧库语义缓存表补 structured / sources 列（SQLite ALTER 幂等容错）。"""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(semantic_cache)").fetchall()]
    if "structured" not in cols:
        conn.execute("ALTER TABLE semantic_cache ADD COLUMN structured TEXT NOT NULL DEFAULT '[]'")
    if "sources" not in cols:
        # 2026-09-17 修 P1-3：缓存回答的**原始来源**（命中时还原给用户/反馈分诊）
        conn.execute("ALTER TABLE semantic_cache ADD COLUMN sources TEXT NOT NULL DEFAULT '[]'")
    if "kind" not in cols:
        # 2026-09-18：来源类型（local=本地知识库，web=联网检索/合并）——决定 TTL 与
        # 命中时能否声称"已确认"
        conn.execute("ALTER TABLE semantic_cache ADD COLUMN kind TEXT NOT NULL DEFAULT 'local'")
    if "limitations" not in cols:
        # 2026-09-18：限制说明必须跟着答案一起存，否则联网答案的"未核实来源""未命中
        # 官方来源"等警示会在命中时丢掉
        conn.execute("ALTER TABLE semantic_cache ADD COLUMN limitations TEXT NOT NULL DEFAULT '[]'")
    conn.commit()


class SemanticCache:
    def __init__(
        self,
        db_path: Path | str,
        *,
        embedder=None,
        threshold: float = DEFAULT_THRESHOLD,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        web_ttl_seconds: float = DEFAULT_WEB_TTL_SECONDS,
    ) -> None:
        self.db_path = Path(db_path)
        self._embedder = embedder  # 注入（测试）；默认延迟复用知识库共享 embedder
        self.threshold = threshold
        self.ttl = ttl_seconds
        self.web_ttl = web_ttl_seconds
        self._lock = threading.RLock()
        self._ready = False

    # ── 基础 ──

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        import sqlite3 as _sqlite3
        try:
            _ensure_columns(conn)
        except Exception:  # noqa: BLE001 — 迁移失败不阻塞（lookup 侧容错）
            pass
        return conn

    def _ensure(self) -> None:
        if not self._ready:
            with self._lock:
                if not self._ready:
                    conn = self._connect()
                    conn.close()
                    self._ready = True

    def _embed(self, text: str) -> list[float]:
        if self._embedder is not None:
            return self._embedder(text)
        from knowledge.vector_store import shared_embedder

        model, _method = shared_embedder()
        return model.encode([text])[0].tolist()

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        try:
            import math

            dot = sum(x * y for x, y in zip(a, b, strict=False))
            na = math.sqrt(sum(x * x for x in a)) or 1e-9
            nb = math.sqrt(sum(y * y for y in b)) or 1e-9
            return dot / (na * nb)
        except Exception:
            return 0.0

    # ── 查询 / 写入 ──

    def lookup(self, question: str, namespace: str, *, now: float | None = None) -> dict[str, Any] | None:
        """语义命中则返回 {answer, score, created_at, cache_id}；否则 None。"""
        if not (question or "").strip():
            return None
        self._ensure()
        timestamp = time.time() if now is None else now
        try:
            query_vec = self._embed(question)
        except Exception:
            return None  # embedding 不可用时缓存整体旁路，不影响问答主链路
        with self._lock, self._connect() as conn:
            # 先按**最长**的 TTL 粗筛，再逐条按自己的 kind 判有效期
            window = max(self.ttl, self.web_ttl)
            rows = conn.execute(
                "SELECT id, question, answer, structured, sources, kind, limitations, embedding, created_at "
                "FROM semantic_cache WHERE namespace = ? AND created_at > ? ORDER BY created_at DESC",
                (namespace, timestamp - window),
            ).fetchall()
        best: dict[str, Any] | None = None
        best_score = 0.0
        for row in rows:
            kind = str(row["kind"] or "local")
            effective_ttl = self.web_ttl if kind == "web" else self.ttl
            if row["created_at"] <= timestamp - effective_ttl:
                continue  # 按类型过期（web 30 分钟 / local 24 小时）
            try:
                vec = json.loads(row["embedding"])
            except (ValueError, TypeError):
                continue
            score = self._cosine(query_vec, vec)
            if score >= self.threshold and score > best_score:
                best_score = score
                best = {
                    "answer": row["answer"],
                    "structured": json.loads(row["structured"] or "[]") or [],
                    "sources": json.loads(row["sources"] or "[]") or [],
                    "kind": kind,
                    "limitations": json.loads(row["limitations"] or "[]") or [],
                    "score": round(score, 4),
                    "created_at": row["created_at"],
                    "cache_id": row["id"],
                }
        if best is not None:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = ?",
                    (best["cache_id"],),
                )
                conn.commit()
        return best

    def store(
        self,
        question: str,
        answer: str,
        namespace: str,
        *,
        source_hashes: list[str] | None = None,
        structured: list[dict] | None = None,
        sources: list[dict] | None = None,
        kind: str = "local",
        limitations: list[str] | None = None,
        now: float | None = None,
    ) -> bool:
        if not (question or "").strip() or not (answer or "").strip():
            return False
        self._ensure()
        timestamp = time.time() if now is None else now
        try:
            vec = self._embed(question)
        except Exception:
            return False
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO semantic_cache(namespace, question, answer, structured, embedding, "
                "source_hashes, sources, kind, limitations, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    namespace,
                    question.strip(),
                    answer.strip(),
                    json.dumps(structured or [], ensure_ascii=False),
                    json.dumps(vec),
                    json.dumps(sorted(set(source_hashes or []))),
                    json.dumps(list(sources or []), ensure_ascii=False),
                    "web" if str(kind) == "web" else "local",
                    json.dumps(list(limitations or []), ensure_ascii=False),
                    timestamp,
                ),
            )
            conn.commit()
        return True

    # ── 失效 / 管理 ──

    def invalidate_missing(self, new_hashes: set[str] | list[str], namespace: str | None = None) -> int:
        """定向失效：答案引用的 chunk hash 不在新发布集合中（依据已变化）则删除。"""
        new_set = {h for h in (new_hashes or []) if h}
        if not new_set:
            return 0
        self._ensure()
        removed = 0
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, source_hashes FROM semantic_cache"
                + (" WHERE namespace = ?" if namespace else ""),
                (namespace,) if namespace else (),
            ).fetchall()
            for row in rows:
                try:
                    hashes = set(json.loads(row["source_hashes"] or "[]"))
                except (ValueError, TypeError):
                    continue
                if hashes and not hashes.issubset(new_set):
                    conn.execute("DELETE FROM semantic_cache WHERE id = ?", (row["id"],))
                    removed += 1
            conn.commit()
        return removed

    def clear(self, namespace: str | None = None) -> int:
        self._ensure()
        with self._lock, self._connect() as conn:
            if namespace:
                cur = conn.execute("DELETE FROM semantic_cache WHERE namespace = ?", (namespace,))
            else:
                cur = conn.execute("DELETE FROM semantic_cache")
            conn.commit()
            return cur.rowcount

    def stats(self, namespace: str | None = None) -> dict[str, Any]:
        self._ensure()
        with self._lock, self._connect() as conn:
            if namespace:
                row = conn.execute(
                    "SELECT COUNT(*) n, COALESCE(SUM(hit_count),0) hits FROM semantic_cache WHERE namespace = ?",
                    (namespace,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) n, COALESCE(SUM(hit_count),0) hits FROM semantic_cache"
                ).fetchone()
            return {"entries": row["n"], "total_hits": row["hits"], "threshold": self.threshold, "ttl_seconds": self.ttl}
