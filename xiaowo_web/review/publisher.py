"""Recoverable full-generation publication to Chroma and BM25 artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from xiaowo_web.review.store import PublishJob, ReviewStore
from xiaowo_web.settings import WebSettings


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: Any) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical_bytes(payload)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_parent(path)
    return path.as_posix(), _digest(data)


@dataclass(frozen=True, slots=True)
class IndexArtifact:
    kind: str
    locator: str
    document_count: int
    content_hash: str


class GenerationWriter(Protocol):
    def write(
        self,
        namespace: str,
        generation_id: str,
        documents: list[dict[str, Any]],
    ) -> IndexArtifact: ...


class Bm25GenerationWriter:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def write(
        self,
        namespace: str,
        generation_id: str,
        documents: list[dict[str, Any]],
    ) -> IndexArtifact:
        from knowledge.vector_store import _tokenize_cjk

        payload = {
            "schema_version": 1,
            "namespace": namespace,
            "generation_id": generation_id,
            "documents": [
                {
                    "document_id": item["document_id"],
                    "content": item["content"],
                    "content_hash": item["content_hash"],
                    "metadata": item["metadata"],
                    "tokens": _tokenize_cjk(item["content"]),
                    "expires_at": item["expires_at"],
                }
                for item in documents
            ],
        }
        path = self.base_dir / namespace / f"{generation_id}.json"
        locator, content_hash = _atomic_json(path, payload)
        verified = json.loads(path.read_text(encoding="utf-8"))
        if len(verified.get("documents") or []) != len(documents):
            raise RuntimeError("BM25 generation document count mismatch")
        return IndexArtifact("bm25", locator, len(documents), content_hash)


class ChromaGenerationWriter:
    def __init__(self, persist_dir: Path, *, embedding_model: Any | None = None) -> None:
        self.persist_dir = Path(persist_dir)
        self._injected_embedding_model = embedding_model

    @staticmethod
    def collection_name(namespace: str, generation_id: str) -> str:
        suffix = hashlib.sha256(generation_id.encode("utf-8")).hexdigest()[:24]
        return f"xw-{namespace}-{suffix}"

    def write(
        self,
        namespace: str,
        generation_id: str,
        documents: list[dict[str, Any]],
    ) -> IndexArtifact:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.persist_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        collection_name = self.collection_name(namespace, generation_id)
        existing = {collection.name for collection in client.list_collections()}
        if collection_name in existing:
            client.delete_collection(collection_name)
        collection = client.create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine", "xiaowo_generation": generation_id},
        )
        if documents:
            contents = [str(item["content"]) for item in documents]
            embedding_model = self._injected_embedding_model
            if embedding_model is None:
                from knowledge.vector_store import FAQVectorStore

                embedding_model = FAQVectorStore().embedding_model
            embeddings = embedding_model.encode(contents).tolist()
            metadatas = []
            for item in documents:
                metadata = {
                    key: value
                    for key, value in dict(item["metadata"]).items()
                    if isinstance(value, (str, int, float, bool))
                }
                metadata["content_hash"] = item["content_hash"]
                metadata["expires_at"] = item["expires_at"]
                metadatas.append(metadata)
            collection.add(
                ids=[item["document_id"] for item in documents],
                embeddings=embeddings,
                documents=contents,
                metadatas=metadatas,
            )
        if collection.count() != len(documents):
            raise RuntimeError("Chroma generation document count mismatch")
        if documents:
            stored = collection.get(include=["metadatas"])
            stored_hashes = sorted((metadata or {}).get("content_hash", "") for metadata in stored["metadatas"])
            expected_hashes = sorted(item["content_hash"] for item in documents)
            if stored_hashes != expected_hashes:
                raise RuntimeError("Chroma generation hash verification failed")
        fingerprint = _digest(_canonical_bytes([
            (item["document_id"], item["content_hash"]) for item in documents
        ]))
        return IndexArtifact("chroma", collection_name, len(documents), fingerprint)


class PublicationWorker:
    def __init__(
        self,
        store: ReviewStore,
        settings: WebSettings,
        *,
        vector_writer: GenerationWriter | None = None,
        bm25_writer: GenerationWriter | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.vector_writer = vector_writer or ChromaGenerationWriter(settings.published_chroma_dir)
        self.bm25_writer = bm25_writer or Bm25GenerationWriter(settings.published_bm25_dir)
        self.worker_id = worker_id or f"publisher-{secrets.token_urlsafe(8)}"

    def run_once(self, *, now: float | None = None) -> str | None:
        self.store.expire_due_chunks(now=now)
        job = self.store.claim_publish_job(self.worker_id, now=now)
        if job is None:
            return None
        try:
            documents = self.store.materialize_publish_documents(job, now=now)
            vector = self.vector_writer.write(job.namespace, job.generation_id, documents)
            bm25 = self.bm25_writer.write(job.namespace, job.generation_id, documents)
            if vector.document_count != bm25.document_count:
                raise RuntimeError("published index counts do not match")
            manifest = {
                "schema_version": 1,
                "namespace": job.namespace,
                "generation_id": job.generation_id,
                "document_count": len(documents),
                "documents_hash": _digest(_canonical_bytes([
                    (item["document_id"], item["content_hash"]) for item in documents
                ])),
                "artifacts": [
                    {
                        "kind": vector.kind,
                        "locator": vector.locator,
                        "document_count": vector.document_count,
                        "content_hash": vector.content_hash,
                    },
                    {
                        "kind": bm25.kind,
                        "locator": bm25.locator,
                        "document_count": bm25.document_count,
                        "content_hash": bm25.content_hash,
                    },
                ],
            }
            manifest_path = (
                Path(self.settings.web_evidence_dir)
                / "approved"
                / "manifests"
                / job.namespace
                / f"{job.generation_id}.json"
            )
            locator, manifest_hash = _atomic_json(manifest_path, manifest)
            activated = self.store.activate_publish_job(
                job,
                manifest_path=locator,
                manifest_hash=manifest_hash,
                now=now,
            )
            return "active" if activated else "orphan"
        except Exception:
            return self.store.fail_publish_job(job, "PUBLISH_FAILED", now=now)
