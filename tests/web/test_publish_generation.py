"""Atomic Chroma/BM25 generation activation and recovery boundaries."""

from __future__ import annotations

import sqlite3
import time

from tests.web.helpers import make_settings
from xiaowo_web.review import ReviewStore
from xiaowo_web.review.publisher import ChromaGenerationWriter, IndexArtifact, PublicationWorker
from xiaowo_web.worker import IngestionWorker


class RecordingWriter:
    def __init__(self, kind: str, *, fail: bool = False) -> None:
        self.kind = kind
        self.fail = fail
        self.calls: list[tuple[str, str, list[dict]]] = []

    def write(self, namespace: str, generation_id: str, documents: list[dict]) -> IndexArtifact:
        self.calls.append((namespace, generation_id, documents))
        if self.fail:
            raise RuntimeError(f"{self.kind} failed")
        return IndexArtifact(self.kind, f"fixture://{self.kind}/{generation_id}", len(documents), f"{self.kind}-hash")


class MutatingWriter(RecordingWriter):
    def __init__(self, kind: str, callback) -> None:
        super().__init__(kind)
        self.callback = callback
        self.called = False

    def write(self, namespace: str, generation_id: str, documents: list[dict]) -> IndexArtifact:
        result = super().write(namespace, generation_id, documents)
        if not self.called:
            self.called = True
            self.callback()
        return result


class _EmbeddingMatrix(list):
    def tolist(self) -> list[list[float]]:
        return list(self)


class DeterministicEmbedding:
    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts: list[str]) -> _EmbeddingMatrix:
        self.calls += 1
        return _EmbeddingMatrix([
            [float(len(text)), float(sum(ord(char) for char in text) % 997), 1.0]
            for text in texts
        ])


def _approved_item(store: ReviewStore, *, suffix: str = "one") -> str:
    store.enqueue_candidate("demo", {
        "source_id": f"s-{suffix}",
        "normalized_url": f"https://example.com/{suffix}",
        "final_url": f"https://example.com/{suffix}",
        "title": f"公开通知 {suffix}",
        "institution": "测试机构",
        "level": "reliable_independent",
        "fetched_at": "2026-08-27T00:00:00Z",
        "content_type": "text/html",
        "snapshot_text": f"公开通知 {suffix} 的正文内容足够长，用于审核发布 generation 测试。",
        "evidence_span_hash": f"span-{suffix}",
    })
    assert IngestionWorker(store, worker_id=f"cleaner-{suffix}").run_once() == "done"
    item = store.list_items("demo", status="draft")[0]
    detail = store.get_item("demo", item["item_id"])
    assert detail is not None
    store.start_review("demo", item["item_id"], "reviewer", f"start-{suffix}")
    current_ids = {
        version["version_id"]
        for version in detail["versions"]
        if version["version_number"] == detail["current_version"]
    }
    chunk = next(value for value in detail["chunks"] if value["version_id"] in current_ids)
    store.set_chunk_approval("demo", item["item_id"], chunk["chunk_id"], True, "reviewer", f"chunk-{suffix}")
    store.approve_item(
        "demo",
        item["item_id"],
        category="announcement",
        ttl_days=7,
        actor_key="reviewer",
        request_id=f"approve-{suffix}",
    )
    return item["item_id"]


def test_generation_activates_only_after_both_indexes_verify(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _approved_item(store)
    vector = RecordingWriter("chroma")
    bm25 = RecordingWriter("bm25")
    worker = PublicationWorker(
        store,
        settings,
        vector_writer=vector,
        bm25_writer=bm25,
        worker_id="publisher-ok",
    )
    assert worker.run_once() == "active"
    active = store.get_active_generation("demo")
    assert active is not None
    assert vector.calls[0][1] == bm25.calls[0][1] == active["generation_id"]
    assert len(vector.calls[0][2]) == 1
    assert store.get_item("demo", item_id)["status"] == "active"


def test_partial_publish_failure_never_changes_active_pointer_and_retries(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _approved_item(store)
    vector = RecordingWriter("chroma")
    broken_bm25 = RecordingWriter("bm25", fail=True)
    failed = PublicationWorker(
        store,
        settings,
        vector_writer=vector,
        bm25_writer=broken_bm25,
        worker_id="publisher-fail",
    )
    start = time.time()
    assert failed.run_once(now=start) == "retry"
    assert store.get_active_generation("demo") is None
    assert store.get_item("demo", item_id)["status"] == "publish_failed"

    recovered = PublicationWorker(
        store,
        settings,
        vector_writer=RecordingWriter("chroma"),
        bm25_writer=RecordingWriter("bm25"),
        worker_id="publisher-recover",
    )
    assert recovered.run_once(now=start + 10) == "active"
    assert store.get_active_generation("demo") is not None
    assert store.get_item("demo", item_id)["status"] == "active"


def test_publish_failure_only_marks_items_linked_to_that_job(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    store = ReviewStore(settings)
    store.initialize()
    linked_item = _approved_item(store, suffix="linked-failure")
    job = store.claim_publish_job("publisher-linked")
    assert job is not None

    unrelated_item = _approved_item(store, suffix="unrelated-failure")
    with sqlite3.connect(settings.review_db_path) as conn:
        conn.execute(
            "UPDATE review_items SET status = 'pending_publish' WHERE item_id = ?",
            (unrelated_item,),
        )
        conn.commit()

    assert store.fail_publish_job(job, "FIXTURE_FAILURE") == "retry"
    assert store.get_item("demo", linked_item)["status"] == "publish_failed"
    assert store.get_item("demo", unrelated_item)["status"] == "pending_publish"


def test_generation_changed_during_build_is_retried_without_activation(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _approved_item(store, suffix="stale-during-build")
    vector = MutatingWriter(
        "chroma",
        lambda: store.revoke_item(
            "demo", item_id, "reviewer", "revoke-during-publication",
        ),
    )
    worker = PublicationWorker(
        store,
        settings,
        vector_writer=vector,
        bm25_writer=RecordingWriter("bm25"),
        worker_id="publisher-stale",
    )

    assert worker.run_once() == "retry"
    assert store.get_active_generation("demo") is None
    assert store.get_item("demo", item_id)["status"] == "revoked"
    with sqlite3.connect(settings.review_db_path) as conn:
        stale_job = conn.execute(
            """
            SELECT status, last_error_code FROM publish_jobs
            WHERE last_error_code = 'PUBLISH_SNAPSHOT_STALE'
            """
        ).fetchone()
        stale_documents = conn.execute(
            """
            SELECT COUNT(*) FROM publish_documents pd
            JOIN publish_jobs pj ON pj.generation_id = pd.generation_id
            WHERE pj.last_error_code = 'PUBLISH_SNAPSHOT_STALE'
            """
        ).fetchone()[0]
    assert stale_job == ("retry", "PUBLISH_SNAPSHOT_STALE")
    assert stale_documents == 0


def test_real_chroma_writer_rebuilds_and_verifies_generation_collection(tmp_path) -> None:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    persist_dir = tmp_path / "published-chroma"
    embedding = DeterministicEmbedding()
    writer = ChromaGenerationWriter(
        persist_dir,
        embedding_model=embedding,
    )
    generation_id = "gen-chroma-fixture"
    first_documents = [
        {
            "document_id": "doc-one",
            "content": "第一份人工审核公开资料。",
            "content_hash": "hash-one",
            "metadata": {"title": "第一份资料", "namespace": "demo"},
            "expires_at": 9999999999.0,
        },
        {
            "document_id": "doc-two",
            "content": "第二份人工审核公开资料。",
            "content_hash": "hash-two",
            "metadata": {"title": "第二份资料", "namespace": "demo"},
            "expires_at": 9999999999.0,
        },
    ]
    first = writer.write("demo", generation_id, first_documents)
    assert first.kind == "chroma"
    assert first.document_count == 2

    replacement = writer.write("demo", generation_id, [first_documents[1]])
    assert replacement.locator == first.locator
    assert replacement.document_count == 1
    assert replacement.content_hash != first.content_hash
    assert embedding.calls == 1

    client = chromadb.PersistentClient(
        path=str(persist_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    assert {collection.name for collection in client.list_collections()} == {first.locator}
    collection = client.get_collection(first.locator)
    stored = collection.get(include=["documents", "metadatas"])
    assert stored["ids"] == ["doc-two"]
    assert stored["documents"] == ["第二份人工审核公开资料。"]
    assert stored["metadatas"][0]["content_hash"] == "hash-two"
    assert stored["metadatas"][0]["expires_at"] == 9999999999.0

    fresh_embedding = DeterministicEmbedding()
    fresh_writer = ChromaGenerationWriter(
        persist_dir,
        embedding_model=fresh_embedding,
    )
    cached = fresh_writer.write("demo", "gen-chroma-cached", [first_documents[1]])
    assert cached.document_count == 1
    assert fresh_embedding.calls == 0


def test_queued_publications_merge_before_the_worker_claims_them(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    store = ReviewStore(settings)
    store.initialize()
    first_item = _approved_item(store, suffix="merge-one")
    second_item = _approved_item(store, suffix="merge-two")

    with sqlite3.connect(settings.review_db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM publish_jobs WHERE status = 'queued'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM publish_generations").fetchone()[0] == 1
        targets = {
            row[0] for row in conn.execute("SELECT item_id FROM publish_job_items")
        }
    assert targets == {first_item, second_item}

    vector = RecordingWriter("chroma")
    worker = PublicationWorker(
        store,
        settings,
        vector_writer=vector,
        bm25_writer=RecordingWriter("bm25"),
        worker_id="publisher-merged",
    )
    assert worker.run_once() == "active"
    assert {document["item_id"] for document in vector.calls[0][2]} == {
        first_item,
        second_item,
    }


def test_only_active_and_previous_generations_remain_rollback_candidates(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    store = ReviewStore(settings)
    store.initialize()
    generations: list[str] = []
    for suffix in ("history-one", "history-two", "history-three"):
        _approved_item(store, suffix=suffix)
        worker = PublicationWorker(
            store,
            settings,
            vector_writer=RecordingWriter("chroma"),
            bm25_writer=RecordingWriter("bm25"),
            worker_id=f"publisher-{suffix}",
        )
        assert worker.run_once() == "active"
        generations.append(str(store.get_active_generation("demo")["generation_id"]))

    with sqlite3.connect(settings.review_db_path) as conn:
        statuses = dict(conn.execute(
            "SELECT generation_id, status FROM publish_generations"
        ).fetchall())
    assert statuses[generations[0]] == "orphan"
    assert statuses[generations[1]] == "verified"
    assert statuses[generations[2]] == "active"


def test_expiry_builds_a_new_empty_generation_instead_of_mutating_active(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _approved_item(store)
    first_vector = RecordingWriter("chroma")
    first = PublicationWorker(
        store,
        settings,
        vector_writer=first_vector,
        bm25_writer=RecordingWriter("bm25"),
        worker_id="publisher-first",
    )
    assert first.run_once() == "active"
    first_generation = store.get_active_generation("demo")["generation_id"]

    with sqlite3.connect(settings.review_db_path) as conn:
        conn.execute("UPDATE review_chunks SET expires_at = ? WHERE item_id = ?", (time.time() - 1, item_id))
        conn.commit()

    second_vector = RecordingWriter("chroma")
    second = PublicationWorker(
        store,
        settings,
        vector_writer=second_vector,
        bm25_writer=RecordingWriter("bm25"),
        worker_id="publisher-expiry",
    )
    assert second.run_once() == "active"
    second_generation = store.get_active_generation("demo")["generation_id"]
    assert second_generation != first_generation
    assert second_vector.calls[0][2] == []
    assert store.get_item("demo", item_id)["status"] == "expired"
