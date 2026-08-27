"""Fail-closed retrieval from the active, human-approved BM25 generation."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from knowledge.vector_store import _BuiltinBM25, _tokenize_cjk
from xiaowo_web.auth.models import Principal
from xiaowo_web.evidence.trust import SourceTrustStore
from xiaowo_web.review import ReviewStore
from xiaowo_web.settings import WebSettings


@dataclass(frozen=True, slots=True)
class _LoadedGeneration:
    generation_id: str
    manifest_hash: str
    documents: tuple[dict[str, Any], ...]
    index: _BuiltinBM25
    manifest_signature: tuple[int, int]
    bm25_signature: tuple[int, int]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _signal_tokens(tokens: list[str]) -> set[str]:
    return {token for token in tokens if len(token) >= 2}


class ApprovedKnowledgeRetriever:
    """Read only the generation selected by SQLite's active pointer."""

    def __init__(
        self,
        store: ReviewStore,
        settings: WebSettings,
        *,
        trust_store: SourceTrustStore | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.trust_store = trust_store or SourceTrustStore()
        self._cache: dict[str, _LoadedGeneration] = {}
        self._cache_lock = threading.RLock()

    def search(
        self,
        query: str,
        principal: Principal,
        *,
        limit: int = 5,
        now: float | None = None,
    ) -> dict[str, Any]:
        namespace = "demo" if principal.auth_mode == "demo" else "production"
        active = self.store.get_active_generation(namespace)
        if active is None:
            return self._empty(namespace, "NO_ACTIVE_APPROVED_INDEX")
        try:
            loaded = self._load(namespace, active)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self._empty(
                namespace,
                "APPROVED_INDEX_INVALID",
                limitations=["已批准知识索引未通过完整性校验，本轮已回退既有本地知识库。"],
            )

        query_tokens = _tokenize_cjk(query)
        signals = _signal_tokens(query_tokens)
        if not query_tokens or not signals:
            return self._empty(namespace, "APPROVED_INDEX_NO_MATCH", loaded.generation_id)
        timestamp = time.time() if now is None else now
        raw_scores = loaded.index.get_scores(query_tokens)
        ranked: list[tuple[bool, float, float, dict[str, Any]]] = []
        for document, raw_score in zip(loaded.documents, raw_scores, strict=True):
            if float(document["expires_at"]) <= timestamp or raw_score <= 0:
                continue
            tokens = set(document["tokens"])
            matched = signals & tokens
            if not matched:
                continue
            overlap = len(matched) / max(1, len(signals))
            threshold_met = (
                len(matched) >= min(2, len(signals))
                and overlap >= 0.2
            )
            score = (
                min(0.99, 0.45 + 0.5 * overlap)
                if threshold_met
                else min(0.41, 0.1 + 0.3 * overlap)
            )
            ranked.append((threshold_met, float(raw_score), score, document))
        ranked.sort(key=lambda item: (-int(item[0]), -item[1], str(item[3]["document_id"])))

        results = [
            self._public_candidate(namespace, loaded.generation_id, document, score, raw_score)
            for _threshold_met, raw_score, score, document in ranked[:min(max(limit, 1), 20)]
        ]
        found = bool(ranked and ranked[0][0])
        return {
            "found": found,
            "results": results,
            "namespace": namespace,
            "generation_id": loaded.generation_id,
            "reason": "APPROVED_INDEX_MATCH" if found else "APPROVED_INDEX_NO_MATCH",
            "limitations": [],
        }

    def validate_generation(self, namespace: str, generation_id: str) -> bool:
        generation = self.store.get_generation(namespace, generation_id)
        if generation is None or generation.get("status") not in {"active", "verified"}:
            return False
        try:
            loaded = self._load(namespace, generation)
            self._validate_chroma(namespace, generation, loaded.documents)
        except Exception:
            return False
        return True

    def _validate_chroma(
        self,
        namespace: str,
        generation: dict[str, Any],
        documents: tuple[dict[str, Any], ...],
    ) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        from xiaowo_web.review.publisher import ChromaGenerationWriter

        generation_id = str(generation["generation_id"])
        manifest_hash = str(generation.get("manifest_hash") or "")
        manifest_path = (
            Path(self.settings.web_evidence_dir)
            / "approved"
            / "manifests"
            / namespace
            / f"{generation_id}.json"
        ).resolve()
        manifest_bytes = manifest_path.read_bytes()
        if _sha256(manifest_bytes) != manifest_hash:
            raise ValueError("manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        artifact = next(
            (
                item
                for item in manifest.get("artifacts") or []
                if isinstance(item, dict) and item.get("kind") == "chroma"
            ),
            None,
        )
        if artifact is None:
            raise ValueError("Chroma artifact missing")
        expected_locator = ChromaGenerationWriter.collection_name(namespace, generation_id)
        if str(artifact.get("locator") or "") != expected_locator:
            raise ValueError("Chroma collection locator mismatch")
        expected_pairs = [
            (str(document["document_id"]), str(document["content_hash"]))
            for document in documents
        ]
        expected_fingerprint = _sha256(_canonical_bytes(expected_pairs))
        if (
            int(artifact.get("document_count") or 0) != len(expected_pairs)
            or int(manifest.get("document_count") or 0) != len(expected_pairs)
            or str(artifact.get("content_hash") or "") != expected_fingerprint
            or str(manifest.get("documents_hash") or "") != expected_fingerprint
        ):
            raise ValueError("Chroma manifest fingerprint mismatch")

        client = chromadb.PersistentClient(
            path=str(self.settings.published_chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        collection = client.get_collection(expected_locator)
        if collection.count() != len(expected_pairs):
            raise ValueError("Chroma document count mismatch")
        if str((collection.metadata or {}).get("xiaowo_generation") or "") != generation_id:
            raise ValueError("Chroma generation metadata mismatch")
        stored = collection.get(include=["metadatas"])
        stored_pairs = sorted(
            (
                str(document_id),
                str((metadata or {}).get("content_hash") or ""),
            )
            for document_id, metadata in zip(
                stored.get("ids") or [],
                stored.get("metadatas") or [],
                strict=True,
            )
        )
        if stored_pairs != sorted(expected_pairs):
            raise ValueError("Chroma document hashes mismatch")

    def _load(self, namespace: str, active: dict[str, Any]) -> _LoadedGeneration:
        generation_id = str(active["generation_id"])
        manifest_hash = str(active["manifest_hash"] or "")
        if not generation_id.startswith("gen-") or len(manifest_hash) != 64:
            raise ValueError("invalid active generation pointer")
        manifest_path = (
            Path(self.settings.web_evidence_dir)
            / "approved"
            / "manifests"
            / namespace
            / f"{generation_id}.json"
        ).resolve()
        bm25_path = (
            Path(self.settings.published_bm25_dir)
            / namespace
            / f"{generation_id}.json"
        ).resolve()
        manifest_root = (Path(self.settings.web_evidence_dir) / "approved" / "manifests" / namespace).resolve()
        bm25_root = (Path(self.settings.published_bm25_dir) / namespace).resolve()
        if not manifest_path.is_relative_to(manifest_root) or not bm25_path.is_relative_to(bm25_root):
            raise ValueError("generation path escapes configured roots")

        with self._cache_lock:
            cached = self._cache.get(namespace)
            if (
                cached is not None
                and cached.generation_id == generation_id
                and cached.manifest_hash == manifest_hash
                and _file_signature(manifest_path) == cached.manifest_signature
                and _file_signature(bm25_path) == cached.bm25_signature
            ):
                return cached

        manifest_bytes = manifest_path.read_bytes()
        if _sha256(manifest_bytes) != manifest_hash:
            raise ValueError("manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        if manifest.get("namespace") != namespace or manifest.get("generation_id") != generation_id:
            raise ValueError("manifest identity mismatch")
        artifacts = manifest.get("artifacts") or []
        bm25_artifact = next(
            (item for item in artifacts if isinstance(item, dict) and item.get("kind") == "bm25"),
            None,
        )
        if bm25_artifact is None:
            raise ValueError("BM25 artifact missing")
        bm25_bytes = bm25_path.read_bytes()
        if _sha256(bm25_bytes) != str(bm25_artifact.get("content_hash") or ""):
            raise ValueError("BM25 artifact hash mismatch")
        payload = json.loads(bm25_bytes)
        if payload.get("namespace") != namespace or payload.get("generation_id") != generation_id:
            raise ValueError("BM25 identity mismatch")
        documents = payload.get("documents") or []
        if (
            not isinstance(documents, list)
            or len(documents) != int(manifest.get("document_count") or 0)
            or len(documents) != int(bm25_artifact.get("document_count") or 0)
        ):
            raise ValueError("BM25 document count mismatch")
        normalized: list[dict[str, Any]] = []
        for document in documents:
            content = str(document.get("content") or "")
            content_hash = str(document.get("content_hash") or "")
            tokens = document.get("tokens")
            expires_at = document.get("expires_at")
            if (
                not content
                or _sha256(content.encode("utf-8")) != content_hash
                or not isinstance(tokens, list)
                or not all(isinstance(token, str) for token in tokens)
                or expires_at is None
            ):
                raise ValueError("invalid BM25 document")
            normalized.append({
                "document_id": str(document.get("document_id") or ""),
                "content": content,
                "content_hash": content_hash,
                "metadata": dict(document.get("metadata") or {}),
                "tokens": list(tokens),
                "expires_at": float(expires_at),
            })
        loaded = _LoadedGeneration(
            generation_id=generation_id,
            manifest_hash=manifest_hash,
            documents=tuple(normalized),
            index=_BuiltinBM25([document["tokens"] for document in normalized]),
            manifest_signature=_file_signature(manifest_path),
            bm25_signature=_file_signature(bm25_path),
        )
        with self._cache_lock:
            self._cache[namespace] = loaded
        return loaded

    def _public_candidate(
        self,
        namespace: str,
        generation_id: str,
        document: dict[str, Any],
        score: float,
        raw_score: float,
    ) -> dict[str, Any]:
        metadata = document["metadata"]
        source = str(metadata.get("source") or "")
        decision = self.trust_store.classify_url_without_dns(source)
        host = (urlsplit(source).hostname or "").casefold()
        tags = list(decision.tags)
        tags.append("human_approved")
        if namespace == "demo":
            tags.append("demo")
        return {
            "id": document["document_id"],
            "content": document["content"],
            "content_hash": document["content_hash"],
            "title": str(metadata.get("title") or "人工审核资料"),
            "source": source,
            "category": str(metadata.get("category") or "stable_general"),
            "subcategory": "人工审核知识",
            "is_official": decision.level == "official_primary",
            "institution": decision.institution or host,
            "source_level": decision.level,
            "score": round(score, 4),
            "retrieval_score": round(raw_score, 4),
            "retrieval_mode": "approved_bm25",
            "namespace": namespace,
            "generation_id": generation_id,
            "fetched_at": metadata.get("fetched_at"),
            "published_at": metadata.get("published_at"),
            "expires_at": document["expires_at"],
            "validity": "active",
            "tags": tags,
        }

    @staticmethod
    def _empty(
        namespace: str,
        reason: str,
        generation_id: str | None = None,
        *,
        limitations: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "found": False,
            "results": [],
            "namespace": namespace,
            "generation_id": generation_id,
            "reason": reason,
            "limitations": limitations or [],
        }
