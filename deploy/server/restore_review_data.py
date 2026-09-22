# -*- coding: utf-8 -*-
"""审核/发布数据恢复脚本（2026-09-03 事故：demo/reset 清空 review.db 发布数据）。

从磁盘发布产物完整重建 demo 命名空间：
- status=active 的 41 条 review_items（title 取 chroma metadata）
- review_chunks/review_versions/publish_documents（与发布索引 ID 一致）
- 4 行 publish_generations（gen-5 active，其余 orphan）+ active_index_state
- web_snapshots（snapshot_id 确定性派生；content_path 尝试 raw hash 匹配）

来源（全部只读）：
- data/web_evidence/approved/bm25/demo/gen-5_*.json       (41 docs 全量)
- data/web_evidence/approved/manifests/demo/gen-*.json     (4 generations)
- knowledge/chroma_db/web_approved/chroma.sqlite3          (title 等 metadata)

安全：先用 sqlite3 backup API 备份 review.db → data/backups/；事务内 INSERT OR IGNORE；
不触碰现有行（reset 后新采集的 7 条快照、种子 draft）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xiaowo_web.settings import WebSettings  # noqa: E402

TTL_LIMITS = {"announcement": 7, "dynamic_service": 30, "policy": 90, "stable_general": 180}
NS = "demo"
RESTORE_ACTOR = "restore-2026-09-03"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _epoch(value) -> float:
    """ISO 时间戳/epoch/None → epoch float。"""
    if value in (None, ""):
        return time.time()
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def _load_chroma_titles(pub_dir: Path) -> dict[str, dict[str, str]]:
    """chroma embedding_metadata → {chunk_id: {title/category/scope/source}}（仅 gen-5 记录）。"""
    db = sqlite3.connect(pub_dir / "chroma.sqlite3")
    rows = db.execute("SELECT id, key, string_value FROM embedding_metadata").fetchall()
    by_id: dict[str, dict[str, str]] = {}
    for rid, key, val in rows:
        if key is None or val is None:
            continue
        by_id.setdefault(rid, {})[key] = val
    out: dict[str, dict[str, str]] = {}
    for rec in by_id.values():
        if rec.get("generation_id", "").startswith("gen-5_"):
            chunk_id = str(rec.get("chunk_id") or "")
            if chunk_id:
                out[chunk_id] = rec
    return out


def main() -> int:
    settings = WebSettings.from_env()
    db_path = settings.review_db_path
    web_evidence_dir = settings.web_evidence_dir
    bm25_dir = settings.published_bm25_dir
    chroma_dir = Path(settings.published_chroma_dir)

    # 0) 定位 gen-5 产物
    bm25_file = sorted(Path(bm25_dir / NS).glob("gen-5_*.json"))
    if not bm25_file:
        print("未找到 gen-5 bm25 产物——磁盘发布产物可能缺失（被清理或从未存在）。")
        print("还原指引：每日备份在 data/backups/daily/<date>/（含 review.db/xiaowo.db/course_data.db/"
              "artifacts.tar.gz/env.txt），最终手段：")
        print("  1) cp data/backups/daily/<最近日期>/review.db data/review.db")
        print("  2) 重启 web（./deploy/server/start_all.sh）")
        print("  3) 备份中的 artifacts.tar.gz 内为 web_evidence/chroma/programs 目录原样副本")
        return 1
    bm25_file = bm25_file[-1]
    gen5 = json.loads(bm25_file.read_text(encoding="utf-8"))
    gen5_id = gen5["generation_id"]
    docs = gen5["documents"]
    print(f"gen-5: {gen5_id} documents={len(docs)}")

    manifest_files = sorted((web_evidence_dir / "approved" / "manifests" / NS).glob("gen-*.json"))
    chroma_titles = _load_chroma_titles(chroma_dir / "web_approved" if (chroma_dir / "web_approved").exists() else chroma_dir)
    print(f"manifests={len(manifest_files)} chroma title 记录(gen-5)={len(chroma_titles)}")

    # 1) 备份
    backups = db_path.parent / "backups"
    backups.mkdir(exist_ok=True)
    backup_path = backups / f"review_pre_restore_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    src.backup(dst)
    dst.close(); src.close()
    print(f"备份: {backup_path}")

    # 2) 组装行
    rows_items, rows_chunks, rows_versions, rows_docs, rows_snaps = [], [], [], [], []
    for doc in docs:
        md = doc.get("metadata") or {}
        item_id = str(md.get("item_id") or "")
        chunk_id = str(md.get("chunk_id") or "")
        if not item_id or not chunk_id:
            continue
        content = str(doc.get("content") or "")
        content_hash = str(doc.get("content_hash") or "")
        expires_at = float(doc.get("expires_at") or time.time())
        source = str(md.get("source") or "")
        category = str(md.get("category") or "stable_general")
        if category not in TTL_LIMITS:
            category = "stable_general"
        fetched = _epoch(md.get("fetched_at"))
        fetched_iso = str(md.get("fetched_at") or "")
        title = (chroma_titles.get(chunk_id) or {}).get("title") or content[:40]
        snap_id = "snap-" + _digest(f"{NS}:{content_hash}")[:24]
        snapshot_hash_for_path = content_hash[:2] + "/" + content_hash + ".txt"
        content_path = f"raw/{snapshot_hash_for_path}" if (web_evidence_dir / "raw" / snapshot_hash_for_path).exists() else ""
        # version 行（model + approved 两个视图）
        model_ver = "ver-restore-" + _digest(f"{item_id}:m")[:20]
        rows_versions.append((model_ver, item_id, 1, "model", content, content_hash, RESTORE_ACTOR, fetched))
        approved_ver = "ver-restore-" + _digest(f"{item_id}:a")[:20]
        rows_versions.append((approved_ver, item_id, 1, "approved", content, content_hash, RESTORE_ACTOR, fetched))
        rows_items.append((item_id, NS, snap_id, title[:200], str(md.get("scope") or "general"),
                           category, TTL_LIMITS[category], "active", 1, fetched, fetched))
        rows_chunks.append((chunk_id, item_id, model_ver, 0, content, content_hash,
                            "approved", 1, RESTORE_ACTOR, fetched, expires_at))
        metadata_json = json.dumps({
            "item_id": item_id, "chunk_id": chunk_id, "title": title,
            "scope": str(md.get("scope") or "general"), "category": category,
            "ttl_days": TTL_LIMITS[category], "source": source,
            "fetched_at": fetched_iso, "namespace": NS, "generation_id": gen5_id,
        }, ensure_ascii=False, separators=(",", ":"))
        rows_docs.append((gen5_id, f"{item_id}:{chunk_id}", item_id, chunk_id, content,
                          content_hash, metadata_json, expires_at))
        rows_snaps.append((snap_id, NS, source, source, content_hash, content_path,
                           "text/html", fetched_iso, fetched))

    print(f"组装: items={len(rows_items)} chunks={len(rows_chunks)} docs={len(rows_docs)} snapshots={len(rows_snaps)} versions={len(rows_versions)}")

    # 3) 事务写入
    conn = sqlite3.connect(db_path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany("INSERT OR IGNORE INTO web_snapshots(snapshot_id, namespace, normalized_url, final_url, snapshot_hash, content_path, content_type, fetched_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)", rows_snaps)
        conn.executemany("INSERT OR IGNORE INTO review_items(item_id, namespace, snapshot_id, title, scope, category, ttl_days, status, current_version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows_items)
        conn.executemany("INSERT OR IGNORE INTO review_versions(version_id, item_id, version_number, kind, content_text, content_hash, actor_key, created_at) VALUES (?,?,?,?,?,?,?,?)", rows_versions)
        conn.executemany("INSERT OR IGNORE INTO review_chunks(chunk_id, item_id, version_id, position, content_text, content_hash, approval_status, approved, approved_by, approved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows_chunks)
        conn.executemany("INSERT OR IGNORE INTO publish_documents(generation_id, document_id, item_id, chunk_id, content_text, content_hash, metadata_json, expires_at) VALUES (?,?,?,?,?,?,?,?)", rows_docs)
        # generations：4 行（gen-5 active，其余 orphan；manifest_hash 重算）
        for mf in manifest_files:
            gen_id = mf.stem
            manifest_hash = _digest(mf.read_bytes().decode("utf-8"))
            status = "active" if gen_id == gen5_id else "orphan"
            locator = f"approved/manifests/{NS}/{gen_id}.json"
            conn.execute(
                "INSERT OR IGNORE INTO publish_generations(generation_id, namespace, status, manifest_path, manifest_hash, created_at, activated_at, orphaned_at) VALUES (?,?,?,?,?,?,?,?)",
                (gen_id, NS, status, locator, manifest_hash,
                 _epoch(md.get('fetched_at')) if False else mf.stat().st_mtime,
                 mf.stat().st_mtime if status == "active" else None,
                 None if status == "active" else mf.stat().st_mtime),
            )
        conn.execute(
            "INSERT OR IGNORE INTO active_index_state(namespace, generation_id, previous_generation_id, activated_at) VALUES (?,?,?,?)",
            (NS, gen5_id, None, time.time()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # 4) 校验
    conn = sqlite3.connect(db_path)
    for table in ("review_items", "review_chunks", "review_versions", "publish_documents", "publish_generations", "web_snapshots", "active_index_state"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n}")
    active = conn.execute("SELECT generation_id, status FROM publish_generations WHERE status='active'").fetchall()
    print("  active generation:", active)
    conn.close()

    from xiaowo_web.review.store import ReviewStore
    from xiaowo_web.knowledge.approved import ApprovedKnowledgeRetriever
    store = ReviewStore(settings)
    retriever = ApprovedKnowledgeRetriever(store, settings)
    from xiaowo_web.auth.models import Principal
    principal = Principal(principal_id="PB25111691", auth_mode="demo",
                          profile={}, is_admin=True, session_key="restore-check")
    result = retriever.search("图书馆 开放时间", principal, limit=3)
    print("检索校验: found =", result.get("found"), "| results =", len(result.get("results") or []),
          "| 样例 =", [(r.get("title") or "")[:20] for r in (result.get("results") or [])][:3])
    return 0


if __name__ == "__main__":
    sys.exit(main())
