"""Bounded review queues and recoverable orphan-generation cleanup."""

from __future__ import annotations

import json
import sqlite3

from tests.web.helpers import make_settings
from tests.web.test_review_worker import _candidate
from xiaowo_web.review import ReviewStore
from xiaowo_web.review.publisher import ChromaGenerationWriter
from xiaowo_web.worker import IngestionWorker


def _settings(tmp_path):
    return make_settings(tmp_path, extra={
        "XIAOWO_JOB_DONE_RETENTION_SECONDS": str(24 * 60 * 60),
        "XIAOWO_JOB_DEAD_RETENTION_SECONDS": str(2 * 24 * 60 * 60),
        "XIAOWO_ORPHAN_GENERATION_RETENTION_SECONDS": str(24 * 60 * 60),
        "XIAOWO_REVIEW_CLEANUP_INTERVAL_SECONDS": "30",
    })


def _draft_item(store: ReviewStore) -> str:
    store.enqueue_candidate(
        "production",
        _candidate(
            "https://example.com/retention",
            "公开资料用于验证任务保留、哈希墓碑和孤儿 generation 清理。",
        ),
    )
    assert IngestionWorker(store, worker_id="retention-worker").run_once() == "done"
    return str(store.list_items("production")[0]["item_id"])


def test_initialize_migrates_legacy_boolean_chunk_approvals(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.review_db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.review_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE review_chunks (
                chunk_id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                content_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0,
                approved_by TEXT,
                approved_at REAL,
                expires_at REAL,
                UNIQUE(version_id, position)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE publish_generations (
                generation_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                status TEXT NOT NULL,
                manifest_path TEXT,
                manifest_hash TEXT,
                created_at REAL NOT NULL,
                activated_at REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO review_chunks(
                chunk_id, item_id, version_id, position, content_text,
                content_hash, approved
            ) VALUES ('legacy-chunk', 'legacy-item', 'legacy-version', 0, '公开内容', 'hash', 1)
            """
        )
        conn.commit()

    store = ReviewStore(settings)
    store.initialize()
    with sqlite3.connect(settings.review_db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(review_chunks)")}
        generation_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(publish_generations)")
        }
        migrated = conn.execute(
            "SELECT approval_status, approved FROM review_chunks WHERE chunk_id = 'legacy-chunk'"
        ).fetchone()
    assert "approval_status" in columns
    assert "orphaned_at" in generation_columns
    assert migrated == ("approved", 1)


def test_terminal_jobs_are_compacted_without_losing_ingestion_idempotency(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    item_id = _draft_item(store)
    refetch = store.queue_refetch(
        "production", item_id, "reviewer", "retention-refetch", now=10,
    )

    with sqlite3.connect(settings.review_db_path) as conn:
        conn.execute("UPDATE ingestion_jobs SET status = 'done', updated_at = 10")
        conn.execute(
            "UPDATE refetch_jobs SET status = 'done', updated_at = 10 WHERE job_id = ?",
            (refetch["job_id"],),
        )
        conn.execute(
            """
            INSERT INTO publish_generations(generation_id, namespace, status, created_at)
            VALUES ('gen-retained-job', 'production', 'verified', 10)
            """
        )
        conn.execute(
            """
            INSERT INTO publish_jobs(
                job_id, namespace, generation_id, reason, status, attempts,
                max_attempts, available_at, created_at, updated_at
            ) VALUES (
                'pub-retained-job', 'production', 'gen-retained-job', 'fixture',
                'done', 1, 5, 10, 10, 10
            )
            """
        )
        idempotency_key = conn.execute(
            "SELECT idempotency_key FROM ingestion_jobs"
        ).fetchone()[0]
        conn.commit()

    result = store.cleanup_retained_state(now=24 * 60 * 60 + 11, force=True)
    assert result["jobs"] == 3
    with sqlite3.connect(settings.review_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingestion_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM refetch_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0] == 0
        tombstone = conn.execute(
            "SELECT terminal_status FROM ingestion_tombstones WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
    assert tombstone == ("done",)

    duplicate = store.enqueue_candidate(
        "production",
        _candidate(
            "https://example.com/retention",
            "公开资料用于验证任务保留、哈希墓碑和孤儿 generation 清理。",
        ),
    )
    assert duplicate["created"] is False
    assert duplicate["status"] == "done"
    assert str(duplicate["job_id"]).startswith("tombstone-")


def test_dead_jobs_use_the_longer_retention_window(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate(
        "production",
        _candidate("https://example.com/dead", "公开死信夹具内容。"),
    )
    with sqlite3.connect(settings.review_db_path) as conn:
        conn.execute("UPDATE ingestion_jobs SET status = 'dead', updated_at = 10")
        conn.commit()

    store.cleanup_retained_state(now=24 * 60 * 60 + 11, force=True)
    with sqlite3.connect(settings.review_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingestion_jobs").fetchone()[0] == 1

    store.cleanup_retained_state(now=2 * 24 * 60 * 60 + 11, force=True)
    with sqlite3.connect(settings.review_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingestion_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT terminal_status FROM ingestion_tombstones").fetchone() == ("dead",)


def test_old_orphan_generation_removes_only_its_bounded_artifacts(tmp_path) -> None:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    settings = _settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    item_id = _draft_item(store)
    detail = store.get_item("production", item_id)
    assert detail is not None
    chunk = detail["chunks"][0]
    generation_id = "gen-orphan-retention"
    with sqlite3.connect(settings.review_db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO publish_generations(generation_id, namespace, status, created_at)
            VALUES (?, 'production', 'orphan', 10)
            """,
            (generation_id,),
        )
        conn.execute(
            """
            INSERT INTO publish_documents(
                generation_id, document_id, item_id, chunk_id, content_text,
                content_hash, metadata_json, expires_at
            ) VALUES (?, 'doc-orphan', ?, ?, '公开孤儿文档', 'fixture-hash', ?, 9999999999)
            """,
            (generation_id, item_id, chunk["chunk_id"], json.dumps({"namespace": "production"})),
        )
        conn.commit()

    manifest = (
        settings.web_evidence_dir
        / "approved"
        / "manifests"
        / "production"
        / f"{generation_id}.json"
    )
    bm25 = settings.published_bm25_dir / "production" / f"{generation_id}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    bm25.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}", encoding="utf-8")
    bm25.write_text("{}", encoding="utf-8")

    client = chromadb.PersistentClient(
        path=str(settings.published_chroma_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection_name = ChromaGenerationWriter.collection_name("production", generation_id)
    client.create_collection(collection_name)

    result = store.cleanup_retained_state(now=24 * 60 * 60 + 11, force=True)
    assert result["generations"] == 1
    assert result["artifact_errors"] == 0
    assert not manifest.exists()
    assert not bm25.exists()
    assert collection_name not in {collection.name for collection in client.list_collections()}
    with sqlite3.connect(settings.review_db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM publish_documents WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM publish_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0] == 0


def test_orphan_retention_starts_when_generation_becomes_orphan(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    generation_id = "gen-recently-orphaned"
    orphaned_at = 10 * 24 * 60 * 60
    with sqlite3.connect(settings.review_db_path) as conn:
        conn.execute(
            """
            INSERT INTO publish_generations(
                generation_id, namespace, status, created_at, orphaned_at
            ) VALUES (?, 'production', 'orphan', 10, ?)
            """,
            (generation_id, orphaned_at),
        )
        conn.commit()

    retained = store.cleanup_retained_state(now=orphaned_at + 100, force=True)
    assert retained["generations"] == 0
    with sqlite3.connect(settings.review_db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM publish_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0] == 1

    removed = store.cleanup_retained_state(
        now=orphaned_at + 24 * 60 * 60 + 1,
        force=True,
    )
    assert removed["generations"] == 1
