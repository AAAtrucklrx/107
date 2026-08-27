"""Refetch, generation rollback, and source-trust proposal governance."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from tests.web.test_approved_retrieval import _approve_and_publish, _principal
from xiaowo_web.evidence.models import CrawledPage, ValidatedUrl
from xiaowo_web.knowledge.approved import ApprovedKnowledgeRetriever
from xiaowo_web.main import create_app
from xiaowo_web.review import ReviewStore
from xiaowo_web.review.publisher import ChromaGenerationWriter
from xiaowo_web.settings import PROJECT_ROOT
from xiaowo_web.worker import IngestionWorker, RefetchWorker


def _candidate(suffix: str, text: str) -> dict:
    return {
        "source_id": f"source-{suffix}",
        "normalized_url": f"https://new.ustc.edu.cn/column/{suffix}",
        "final_url": f"https://new.ustc.edu.cn/column/{suffix}",
        "title": f"公开资料 {suffix}",
        "institution": "科大域名来源（未审核栏目）",
        "level": "general",
        "fetched_at": "2026-08-27T00:00:00Z",
        "content_type": "text/html",
        "snapshot_text": text,
        "evidence_span_hash": f"span-{suffix}",
    }


def _draft(store: ReviewStore, suffix: str, text: str) -> str:
    store.enqueue_candidate("demo", _candidate(suffix, text))
    assert IngestionWorker(store, worker_id=f"cleaner-{suffix}").run_once() == "done"
    item = next(value for value in store.list_items("demo") if value["title"].endswith(suffix))
    return str(item["item_id"])


def _login(client: TestClient) -> str:
    csrf, _ = bootstrap(client)
    login = client.post("/api/v1/auth/demo", headers=mutation_headers(csrf))
    assert login.status_code == 200
    return str(login.json()["csrf_token"])


class _AllowPublicGuard:
    def validate(self, value: str) -> ValidatedUrl:
        parts = urlsplit(value)
        return ValidatedUrl(
            normalized_url=value,
            scheme=parts.scheme,
            host=parts.hostname or "",
            port=parts.port or 443,
            path=parts.path or "/",
            approved_ips=("202.38.64.1",),
            ustc_domain=True,
        )


class _Crawler:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown

    async def health(self) -> bool:
        return True

    async def crawl(self, url: str) -> CrawledPage:
        return CrawledPage(
            requested_url=url,
            final_url=url,
            title="重新抓取的公开资料",
            markdown=self.markdown,
            status_code=200,
            content_type="text/html",
            fetched_at=datetime.now(UTC).isoformat(),
            published_at=None,
            content_hash=hashlib.sha256(self.markdown.encode("utf-8")).hexdigest(),
            robots_allowed=True,
            peer_ip_verified=True,
        )


def test_refetch_api_is_idempotent_and_worker_only_queues_changed_content(tmp_path) -> None:
    original = "原始公开通知正文足够长，用于验证复抓不会覆盖不可变快照。"
    changed = "更新后的公开通知正文足够长，变化后只能重新进入人工审核队列。"
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _draft(store, "refetch", original)
    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf = _login(client)
        first = client.post(
            f"/api/v1/admin/review-items/{item_id}/refetch",
            headers=mutation_headers(csrf),
        )
        second = client.post(
            f"/api/v1/admin/review-items/{item_id}/refetch",
            headers=mutation_headers(csrf),
        )
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert first.json()["created"] is True
        assert second.json()["created"] is False

        worker = RefetchWorker(
            store,
            _Crawler(changed),
            url_guard=_AllowPublicGuard(),
            worker_id="refetch-changed",
        )
        assert asyncio.run(worker.run_once()) == "ingestion_queued"
        assert store.get_item("demo", item_id)["raw_snapshot"] == original

        queued_again = client.post(
            f"/api/v1/admin/review-items/{item_id}/refetch",
            headers=mutation_headers(csrf),
        )
        assert queued_again.status_code == 202
        unchanged_worker = RefetchWorker(
            store,
            _Crawler(original),
            url_guard=_AllowPublicGuard(),
            worker_id="refetch-unchanged",
        )
        assert asyncio.run(unchanged_worker.run_once()) == "unchanged"

    with sqlite3.connect(settings.review_db_path) as conn:
        outcomes = [
            row[0]
            for row in conn.execute("SELECT outcome FROM refetch_jobs ORDER BY created_at, job_id")
        ]
        queued_ingestion = conn.execute(
            "SELECT COUNT(*) FROM ingestion_jobs WHERE status = 'queued'"
        ).fetchone()[0]
    assert sorted(outcomes) == ["ingestion_queued", "unchanged"]
    assert queued_ingestion == 1


def test_verified_previous_generation_can_be_rolled_back_through_api(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    first_item = _approve_and_publish(
        store,
        settings,
        namespace="demo",
        suffix="rollback-first",
        text="第一版公开知识说明图书馆服务时间，以本条审核资料为准。",
        real_vector=True,
    )
    first_generation = store.get_active_generation("demo")["generation_id"]
    second_item = _approve_and_publish(
        store,
        settings,
        namespace="demo",
        suffix="rollback-second",
        text="第二版新增公开知识说明校园班车安排，以本条审核资料为准。",
        real_vector=True,
    )
    second_generation = store.get_active_generation("demo")["generation_id"]
    assert first_generation != second_generation

    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf = _login(client)
        state = client.get("/api/v1/admin/generations")
        assert state.status_code == 200
        assert state.json()["can_rollback"] is True
        assert state.json()["previous_generation_id"] == first_generation
        response = client.post(
            "/api/v1/admin/generations/rollback",
            headers=mutation_headers(csrf),
        )
        assert response.status_code == 200
        assert response.json()["generation_id"] == first_generation

    assert store.get_active_generation("demo")["generation_id"] == first_generation
    assert store.get_item("demo", first_item)["status"] == "active"
    assert store.get_item("demo", second_item)["status"] == "expired"
    retriever = ApprovedKnowledgeRetriever(store, settings)
    assert retriever.search("图书馆服务时间", _principal("demo"))["found"] is True
    assert retriever.search("校园班车安排", _principal("demo"))["found"] is False


def test_rollback_rejects_a_previous_generation_with_missing_chroma(tmp_path) -> None:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    _approve_and_publish(
        store,
        settings,
        namespace="demo",
        suffix="rollback-corrupt-first",
        text="第一版公开资料用于验证损坏的向量 generation 不得被重新激活。",
        real_vector=True,
    )
    previous_generation = store.get_active_generation("demo")["generation_id"]
    _approve_and_publish(
        store,
        settings,
        namespace="demo",
        suffix="rollback-corrupt-second",
        text="第二版公开资料保持当前 active 指针，回滚失败时不得改变。",
        real_vector=True,
    )
    current_generation = store.get_active_generation("demo")["generation_id"]

    chroma = chromadb.PersistentClient(
        path=str(settings.published_chroma_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    chroma.delete_collection(
        ChromaGenerationWriter.collection_name("demo", previous_generation),
    )

    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf = _login(client)
        response = client.post(
            "/api/v1/admin/generations/rollback",
            headers=mutation_headers(csrf),
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GENERATION_INTEGRITY_INVALID"
    assert store.get_active_generation("demo")["generation_id"] == current_generation


def test_source_trust_proposal_exports_diff_without_mutating_config(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    item_id = _draft(
        store,
        "proposal",
        "科大新栏目公开正文足够长，用于验证白名单建议只能导出而不能即时生效。",
    )
    config_path = PROJECT_ROOT / "config" / "source_trust.yaml"
    original = config_path.read_bytes()
    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf = _login(client)
        mismatched = client.post(
            f"/api/v1/admin/review-items/{item_id}/source-trust-proposals",
            json={
                "host": "other.ustc.edu.cn",
                "path_prefix": "/",
                "level": "official_primary",
                "institution": "测试机构",
                "effective_from": "2026-08-27",
                "rationale": "该建议仅用于验证来源绑定约束。",
            },
            headers=mutation_headers(csrf),
        )
        assert mismatched.status_code == 422
        assert mismatched.json()["error"]["code"] == "SOURCE_PROPOSAL_MISMATCH"

        created = client.post(
            f"/api/v1/admin/review-items/{item_id}/source-trust-proposals",
            json={
                "host": "new.ustc.edu.cn",
                "path_prefix": "/column",
                "level": "official_primary",
                "institution": "中国科学技术大学测试栏目",
                "effective_from": "2026-08-27",
                "rationale": "栏目归属需由人工核验后通过 Git 审查生效。",
            },
            headers=mutation_headers(csrf),
        )
        assert created.status_code == 201
        assert config_path.read_bytes() == original

        exported = client.post(
            "/api/v1/admin/source-trust-proposals/export",
            headers=mutation_headers(csrf),
        )
        assert exported.status_code == 200
        payload = exported.json()
        assert payload["namespace"] == "demo"
        assert "source-trust-demo" in payload["filename"]
        assert "+  host: new.ustc.edu.cn" in payload["diff"]
        assert "+  path_prefix: /column" in payload["diff"]
        assert config_path.read_bytes() == original

        empty = client.post(
            "/api/v1/admin/source-trust-proposals/export",
            headers=mutation_headers(csrf),
        )
        assert empty.status_code == 409
        assert empty.json()["error"]["code"] == "SOURCE_PROPOSAL_EMPTY"
