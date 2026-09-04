"""语义缓存：公共知识问答的「问题→答案」加速层（v1）。

- 命中：query embedding 与缓存条目余弦相似度 ≥ 阈值（默认 0.92，宁缺勿滥）且未过期
- 失效：知识发布激活时按 chunk content_hash 对比定向清理——答案引用的 chunk
  hash 不在新发布集合中（依据已变化）则该条目失效；未受影响的缓存保留
- 存储：独立 SQLite（与 review.db 同目录），条目量小（数百级），逐条余弦足矣
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    embedding TEXT NOT NULL,
    source_hashes TEXT NOT NULL,
    created_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_semantic_cache_ns
    ON semantic_cache(namespace, created_at);
"""


class SemanticCache:
    def __init__(
        self,
        db_path: Path | str,
        *,
        embedder=None,
        threshold: float = DEFAULT_THRESHOLD,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.db_path = Path(db_path)
        self._embedder = embedder  # 注入（测试）；默认延迟复用知识库共享 embedder
        self.threshold = threshold
        self.ttl = ttl_seconds
        self._lock = threading.RLock()
        self._ready = False

    # ── 基础 ──

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
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
            rows = conn.execute(
                "SELECT id, question, answer, embedding, created_at FROM semantic_cache "
                "WHERE namespace = ? AND created_at > ? ORDER BY created_at DESC",
                (namespace, timestamp - self.ttl),
            ).fetchall()
        best: dict[str, Any] | None = None
        best_score = 0.0
        for row in rows:
            try:
                vec = json.loads(row["embedding"])
            except (ValueError, TypeError):
                continue
            score = self._cosine(query_vec, vec)
            if score >= self.threshold and score > best_score:
                best_score = score
                best = {
                    "answer": row["answer"],
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
                "INSERT INTO semantic_cache(namespace, question, answer, embedding, source_hashes, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    namespace,
                    question.strip(),
                    answer.strip(),
                    json.dumps(vec),
                    json.dumps(sorted(set(source_hashes or []))),
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
