"""Active approved-generation retrieval, integrity, TTL, and principal isolation."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from tests.web.helpers import make_settings
from xiaowo_web.auth.models import Principal
from xiaowo_web.chat.models import QaRunRequest
from xiaowo_web.chat.runner import LegacyQaRunner
from xiaowo_web.knowledge.approved import ApprovedKnowledgeRetriever
from xiaowo_web.review import ReviewStore
from xiaowo_web.review.publisher import (
    Bm25GenerationWriter,
    ChromaGenerationWriter,
    IndexArtifact,
    PublicationWorker,
)
from xiaowo_web.worker import IngestionWorker


class _VerifiedVectorWriter:
    def write(self, _namespace: str, generation_id: str, documents: list[dict]) -> IndexArtifact:
        return IndexArtifact("chroma", f"fixture-{generation_id}", len(documents), "fixture-hash")


class _EmbeddingMatrix(list):
    def tolist(self) -> list[list[float]]:
        return list(self)


class _DeterministicEmbedding:
    def encode(self, texts: list[str]) -> _EmbeddingMatrix:
        return _EmbeddingMatrix([
            [float(len(text)), float(sum(ord(char) for char in text) % 997), 1.0]
            for text in texts
        ])


def _principal(auth_mode: str) -> Principal:
    principal_id = "PB25111691" if auth_mode == "demo" else ""
    return Principal(principal_id, auth_mode, {}, auth_mode == "demo", f"session-{auth_mode}")


def _approve_and_publish(
    store: ReviewStore,
    settings,
    *,
    namespace: str,
    suffix: str,
    text: str,
    real_vector: bool = False,
) -> str:
    store.enqueue_candidate(namespace, {
        "source_id": f"source-{suffix}",
        "normalized_url": f"https://www.teach.ustc.edu.cn/notices/{suffix}",
        "final_url": f"https://www.teach.ustc.edu.cn/notices/{suffix}",
        "title": f"公开通知 {suffix}",
        "institution": "中国科学技术大学教务处",
        "level": "official_primary",
        "fetched_at": "2026-08-27T00:00:00Z",
        "content_type": "text/html",
        "snapshot_text": text,
        "evidence_span_hash": f"span-{suffix}",
    })
    assert IngestionWorker(store, worker_id=f"cleaner-{suffix}").run_once() == "done"
    item = next(value for value in store.list_items(namespace) if value["title"].endswith(suffix))
    item_id = str(item["item_id"])
    store.start_review(namespace, item_id, "reviewer", f"start-{suffix}")
    detail = store.get_item(namespace, item_id)
    assert detail is not None
    current_ids = {
        version["version_id"]
        for version in detail["versions"]
        if version["version_number"] == detail["current_version"]
    }
    chunk = next(value for value in detail["chunks"] if value["version_id"] in current_ids)
    store.set_chunk_approval(
        namespace, item_id, chunk["chunk_id"], True, "reviewer", f"chunk-{suffix}",
    )
    store.approve_item(
        namespace,
        item_id,
        category="announcement",
        ttl_days=7,
        actor_key="reviewer",
        request_id=f"approve-{suffix}",
    )
    worker = PublicationWorker(
        store,
        settings,
        vector_writer=(
            ChromaGenerationWriter(
                settings.published_chroma_dir,
                embedding_model=_DeterministicEmbedding(),
            )
            if real_vector
            else _VerifiedVectorWriter()
        ),
        bm25_writer=Bm25GenerationWriter(settings.published_bm25_dir),
        worker_id=f"publisher-{suffix}",
    )
    assert worker.run_once() == "active"
    return item_id


def test_active_generation_is_integrity_checked_and_principal_isolated(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    _approve_and_publish(
        store,
        settings,
        namespace="demo",
        suffix="demo-window",
        text="演示选课服务窗口在九月一日开放，九月三日关闭，仅用于演示。",
    )
    _approve_and_publish(
        store,
        settings,
        namespace="production",
        suffix="production-card",
        text="学生证补办应通过教务处公开流程提交材料，并以当前通知为准。",
    )
    retriever = ApprovedKnowledgeRetriever(store, settings)

    demo = retriever.search("演示选课服务窗口什么时候开放", _principal("demo"))
    assert demo["found"] is True
    assert demo["namespace"] == "demo"
    assert demo["results"][0]["title"] == "公开通知 demo-window"
    assert demo["results"][0]["source_level"] == "official_primary"
    assert demo["results"][0]["generation_id"] == store.get_active_generation("demo")["generation_id"]

    anonymous_cannot_read_demo = retriever.search(
        "演示选课服务窗口什么时候开放", _principal("anonymous"),
    )
    assert anonymous_cannot_read_demo["namespace"] == "production"
    assert anonymous_cannot_read_demo["found"] is False
    assert all(item["namespace"] == "production" for item in anonymous_cannot_read_demo["results"])

    production = retriever.search("学生证如何补办", _principal("anonymous"))
    assert production["found"] is True
    assert production["namespace"] == "production"
    assert all(item["namespace"] == "production" for item in production["results"])


def test_expired_or_tampered_active_documents_fail_closed(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    _approve_and_publish(
        store,
        settings,
        namespace="demo",
        suffix="expiry",
        text="演示活动报名窗口本周开放，资料只用于过期检查。",
    )
    active = store.get_active_generation("demo")
    assert active is not None

    expired = ApprovedKnowledgeRetriever(store, settings).search(
        "演示活动报名窗口",
        _principal("demo"),
        now=time.time() + 8 * 24 * 60 * 60,
    )
    assert expired["found"] is False
    assert expired["results"] == []

    bm25_path = Path(settings.published_bm25_dir) / "demo" / f"{active['generation_id']}.json"
    bm25_path.write_text("{}", encoding="utf-8")
    tampered = ApprovedKnowledgeRetriever(store, settings).search(
        "演示活动报名窗口", _principal("demo"),
    )
    assert tampered["found"] is False
    assert tampered["results"] == []
    assert tampered["reason"] == "APPROVED_INDEX_INVALID"


def test_legacy_runner_injects_approved_candidates_into_supported_graphs() -> None:
    captured: dict = {}

    class _Retriever:
        def search(self, _question: str, _principal: Principal) -> dict:
            return {
                "found": True,
                "results": [{
                    "id": "approved-1",
                    "content": "人工批准的公开资料正文。",
                    "title": "人工批准资料",
                    "source": "https://www.teach.ustc.edu.cn/approved",
                    "score": 0.88,
                    "source_level": "official_primary",
                }],
                "limitations": [],
            }

    def fake_run_qa(
        _question: str,
        *,
        supplemental_candidates: list[dict],
        supplemental_candidates_found: bool,
        **_kwargs,
    ) -> dict:
        captured["candidates"] = supplemental_candidates
        captured["found"] = supplemental_candidates_found
        return {
            "answer": "人工批准资料已用于回答。",
            "intent": "知识问答",
            "candidates": supplemental_candidates,
            "candidates_found": supplemental_candidates_found,
            "tool_results": [],
            "error": "",
        }

    request = QaRunRequest(
        run_id="run-approved",
        question="批准资料是什么？",
        requested_mode="local",
        effective_mode="local",
        principal=_principal("anonymous"),
        conversation_id=None,
    )
    runner = LegacyQaRunner(fake_run_qa, approved_retriever=_Retriever())
    try:
        answer = asyncio.run(runner.run(request))
    finally:
        runner.close()

    assert captured["found"] is True
    assert captured["candidates"][0]["id"] == "approved-1"
    assert answer.claims[0]["status"] == "confirmed"
    assert answer.sources[0]["display_url"] == "https://www.teach.ustc.edu.cn/approved"
