"""回答反馈闭环（2026-09-17）：来源清单、自动转动作、状态流转、质量统计。

背景：反馈此前只写不读、读完也做不了任何事（`status` 永远停在 `open`）。现在：
- 提交时把**该次回答用到的来源**一起存下（闭环依据）；
- `outdated` → 排队复抓；`source_issue` → 来源降级提案；`helpful` → 直接办结；
  `incorrect` → 附来源转人工；做不到就退回 `open` 并写明原因；
- 审核人可改状态（处理中/已办结/已忽略）；
- 反馈按分类/状态进日报统计。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.feedback_triage import triage_feedback
from xiaowo_web.main import create_app
from xiaowo_web.review import ReviewStore
from xiaowo_web.storage.web_store import WebStore
from xiaowo_web.worker import IngestionWorker

_URL = "https://lib.ustc.edu.cn/cat_news/notice/"


def _review_item(store: ReviewStore, url: str = _URL) -> str:
    store.enqueue_candidate("demo", {
        "source_id": "s1",
        "normalized_url": url,
        "final_url": url,
        "title": "图书馆公告",
        "institution": "中国科学技术大学",
        "level": "official_primary",
        "fetched_at": "2026-09-17T00:00:00Z",
        "content_type": "text/html",
        "snapshot_text": "图书馆开放时间公告正文，内容足够长以便入库。" * 3,
        "evidence_span_hash": "span-fb",
    })
    assert IngestionWorker(store, worker_id="w-fb").run_once() == "done"
    return str(store.list_items("demo")[0]["item_id"])


def _count(db, table: str) -> int:
    with sqlite3.connect(db) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# ---------------------------------------------------------------- 存储层

def test_create_feedback_keeps_sources_and_status_flow(tmp_path) -> None:
    # 反馈说明是加密存储的：配了 data_key 才能带说明（生产也缺这个 key，见 .env）
    settings = make_settings(tmp_path, extra={"XIAOWO_DATA_KEY": "unit-test-data-key-0123456789"})
    store = WebStore(settings)
    store.initialize()
    sources = [{"display_url": _URL, "domain": "lib.ustc.edu.cn", "level": "official_primary"}]
    feedback_id = store.create_feedback(
        answer_id="ans-1", run_id="run-1", category="outdated",
        detail="开放时间变了", namespace="demo", sources=sources,
    )
    row = store.get_feedback(feedback_id)
    assert row is not None
    assert row["status"] == "open"
    assert row["sources"] == sources, "来源清单必须留库（闭环依据）"

    updated = store.update_feedback_status(
        feedback_id, status="handled", handled_by="PB25111691", resolution="已核对"
    )
    assert updated["status"] == "handled"
    assert updated["handled_by"] == "PB25111691"
    assert updated["resolution"] == "已核对"

    with sqlite3.connect(settings.app_db_path) as conn:
        raw = conn.execute("SELECT detail FROM answer_feedback WHERE id = ?", (feedback_id,)).fetchone()[0]
    assert raw and raw != "开放时间变了", "说明字段必须加密存储（或 plain 前缀），不能明文"

    try:
        store.update_feedback_status(feedback_id, status="bogus", handled_by="x")
        raise AssertionError("非法状态应当报错")
    except ValueError:
        pass


def test_feedback_stats_counts_by_status_and_category(tmp_path) -> None:
    settings = make_settings(tmp_path)
    store = WebStore(settings)
    store.initialize()
    for category, status in (("outdated", "handled"), ("incorrect", "in_progress"), ("incorrect", "open")):
        fid = store.create_feedback(
            answer_id=f"a-{category}-{status}", run_id="r", category=category,
            detail="", namespace="demo",
        )
        store.update_feedback_status(fid, status=status, handled_by="tester")
    stats = store.feedback_stats("demo")
    assert stats["total"] == 3
    assert stats["handled"] == 1 and stats["in_progress"] == 1 and stats["open"] == 1
    assert stats["by_category"] == {"outdated": 1, "incorrect": 2}


def test_answer_sources_of_unknown_run_is_empty(tmp_path) -> None:
    store = WebStore(make_settings(tmp_path))
    store.initialize()
    assert store.answer_sources("no-such-run", "no-such-answer") == []


# ---------------------------------------------------------------- 分诊

def test_helpful_closes_immediately(tmp_path) -> None:
    store = ReviewStore(make_settings(tmp_path))
    store.initialize()
    status, resolution = triage_feedback(
        review_store=store, feedback_id=1, namespace="demo", category="helpful", sources=[]
    )
    assert status == "handled" and "有帮助" in resolution


def test_outdated_queues_refetch(tmp_path) -> None:
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    _review_item(store)
    status, resolution = triage_feedback(
        review_store=store, feedback_id=7, namespace="demo", category="outdated",
        sources=[{"display_url": _URL}],
    )
    assert status == "in_progress"
    assert "排队复抓" in resolution
    assert _count(settings.review_db_path, "refetch_jobs") == 1


def test_source_issue_creates_downgrade_proposal_without_user_text(tmp_path) -> None:
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    _review_item(store)
    status, resolution = triage_feedback(
        review_store=store, feedback_id=9, namespace="demo", category="source_issue",
        sources=[{"display_url": _URL}],
    )
    assert status == "in_progress" and "降级提案" in resolution
    with sqlite3.connect(settings.review_db_path) as conn:
        raw = conn.execute("SELECT proposal_json FROM source_trust_proposals").fetchone()[0]
    proposal = json.loads(raw)
    assert proposal["host"] == "lib.ustc.edu.cn"
    assert proposal["level"] == "general"
    # ⚠️ 提案会被导出成 Git diff 提交到仓库 → 绝不能带用户原话
    assert "反馈编号 9" in proposal["rationale"]
    assert "用户原话哨兵" not in raw


def test_incorrect_goes_to_human_without_auto_change(tmp_path) -> None:
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    _review_item(store)
    status, resolution = triage_feedback(
        review_store=store, feedback_id=11, namespace="demo", category="incorrect",
        sources=[{"display_url": _URL}],
    )
    assert status == "in_progress" and "人工核对" in resolution
    assert _count(settings.review_db_path, "refetch_jobs") == 0
    assert _count(settings.review_db_path, "source_trust_proposals") == 0


def test_unmatched_or_anonymous_sources_stay_open(tmp_path) -> None:
    store = ReviewStore(make_settings(tmp_path))
    store.initialize()
    # 来源没进审核库 → 明确退回待处理，不假装处理过
    status, resolution = triage_feedback(
        review_store=store, feedback_id=2, namespace="demo", category="outdated",
        sources=[{"display_url": "https://example.com/never-ingested"}],
    )
    assert status == "open" and "还没进审核库" in resolution
    # 匿名命名空间没有审核库对应 → 也不做自动动作
    status2, resolution2 = triage_feedback(
        review_store=store, feedback_id=3, namespace="anonymous", category="outdated",
        sources=[{"display_url": _URL}],
    )
    assert status2 == "open" and resolution2
    # 没有来源（未附引用）
    status3, resolution3 = triage_feedback(
        review_store=store, feedback_id=4, namespace="demo", category="outdated", sources=[]
    )
    assert status3 == "open" and "可追溯" in resolution3


# ---------------------------------------------------------------- 接口

def test_admin_can_update_feedback_status(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    web_store = WebStore(settings)
    web_store.initialize()
    feedback_id = web_store.create_feedback(
        answer_id="a1", run_id="r1", category="other", detail="", namespace="demo",
    )
    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        login = client.post("/api/v1/auth/demo", headers=mutation_headers(csrf))
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]   # 登录后令牌会轮换
        # 管理页能看到它
        page = client.get("/api/v1/admin/feedback")
        assert page.status_code == 200
        assert [item["id"] for item in page.json()["items"]] == [feedback_id]
        # 状态流转
        patched = client.patch(
            f"/api/v1/admin/feedback/{feedback_id}",
            json={"status": "ignored", "resolution": "已看过，无需处理"},
            headers=mutation_headers(csrf),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["status"] == "ignored"
        # 非法状态
        bad = client.patch(
            f"/api/v1/admin/feedback/{feedback_id}",
            json={"status": "nonsense"},
            headers=mutation_headers(csrf),
        )
        assert bad.status_code == 422
        # 不存在的反馈
        missing = client.patch(
            "/api/v1/admin/feedback/999999",
            json={"status": "handled"},
            headers=mutation_headers(csrf),
        )
        assert missing.status_code == 404


def test_daily_stats_include_feedback_signal(tmp_path) -> None:
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    web_store = WebStore(settings)
    web_store.initialize()
    for category in ("outdated", "source_issue", "helpful"):
        web_store.create_feedback(
            answer_id=f"a-{category}", run_id="r", category=category, detail="", namespace="demo"
        )
    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        assert client.post("/api/v1/auth/demo", headers=mutation_headers(csrf)).status_code == 200
        stats = client.get("/api/v1/admin/review-items/stats").json()
    assert stats["feedback"]["total"] == 3
    assert stats["feedback"]["by_category"]["source_issue"] == 1
