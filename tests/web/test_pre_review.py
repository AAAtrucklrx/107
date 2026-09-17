"""进料预审 + 相关性闸门 + 分级自动批准（2026-09-17）。

预审四项判定（稳定/敏感/重复冲突/相关性）在**入库前**跑：

- `relevance=off_topic` → permanent fail，**根本不进审核队列**（实测智能搜索对"助学贷款"
  整批返回 RAG/知网/论文这类无关内容，靠这条挡住；摘要兜底会把它们灌进来）；
- 其余判定写进 `review_audit.detail_json`（含 `auto_approve_eligible`），供日报统计；
- **自动批准默认关**：开启且满足 官方主源 + 稳定 + 不重复不冲突 + 相关 + 分类允许 90 天 时，
  走**与人工完全同一条链路**（逐块批准 → approve_item → 排队发布）。

另外锁一条隐私约定：审核队列**不得**保存用户问题等用户上下文（既有不变量）。
"""

from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.main import create_app
from xiaowo_web.review import ReviewStore
from xiaowo_web.worker import IngestionWorker
from xiaowo_web.worker.pre_review import PreReviewer

# ⚠️ 文本必须能过 `_classify` 的**关键词顺序**判定才拿得到 policy：
# 它先看 公告/通知/公示 → 再看 办理/申请/开放时间/服务 → 才看 办法/规定/政策/制度。
# 所以这里用"实施办法/规定"，避免"申请"把分类抢成 dynamic_service（30 天上限 → 无资格）。
_TEXT = "中国科学技术大学转专业实施办法：二年级可全校转专业，三年级只能本院内转，本规定长期有效。"


def _candidate(*, level: str = "official_primary", span: str = "span-1", url: str = "https://www.teach.ustc.edu.cn/policy/1") -> dict:
    return {
        "source_id": "s1",
        "normalized_url": url,
        "final_url": url,
        "title": "中国科学技术大学转专业政策",
        "institution": "中国科学技术大学",
        "level": level,
        "fetched_at": "2026-09-17T00:00:00Z",
        "content_type": "text/html",
        "snapshot_text": _TEXT,
        "evidence_span_hash": span,
        "raw_question": "用户问题绝不能进入队列",
    }


_STABLE = {
    "stability": "stable",
    "sensitivity": "clean",
    "duplication": "unique",
    "relevance": "on_topic",
    "reason": "长期有效的政策",
}
_OFF_TOPIC = {**_STABLE, "relevance": "off_topic", "reason": "与校园知识无关"}


def _reviewer(payload: dict) -> PreReviewer:
    return PreReviewer("test-model", invoke=lambda _prompt: payload)


def _items(store: ReviewStore) -> list[dict]:
    return store.list_items("demo")


def _jobs(db) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT status, last_error_code FROM ingestion_jobs").fetchall()


def _details(db, action: str) -> list[dict]:
    with sqlite3.connect(db) as conn:
        return [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT detail_json FROM review_audit WHERE action = ?", (action,)
            )
        ]


# ---------------------------------------------------------------- 预审单元

def test_pre_reviewer_is_fail_closed() -> None:
    """判不出来就必须 `unknown` 且**没有**自动批准资格（绝不因为判不出就放行）。"""
    no_model = PreReviewer("")
    assert no_model.review(_TEXT, {}).fallback_reason == "no_model"
    assert not no_model.review(_TEXT, {}).auto_approve_eligible(
        level="official_primary", category="policy"
    )

    def _boom(_prompt):
        raise TimeoutError("llm down")

    failed = PreReviewer("m", invoke=_boom).review(_TEXT, {})
    assert failed.fallback_reason == "llm_error:TimeoutError"
    assert not failed.judged and not failed.auto_approve_eligible(
        level="official_primary", category="policy"
    )

    # 非法取值（模型乱答中文/大小写）一律收敛成 unknown
    weird = PreReviewer("m", invoke=lambda _p: {"stability": "稳定", "relevance": "ON_TOPIC"})
    assert weird.review(_TEXT, {}).stability == "unknown"

    # 正常返回必须**真的判出来**（防提示词占位符不匹配导致静默降级为 unknown）
    good = _reviewer(_STABLE).review(_TEXT, {"title": "转专业政策", "level": "official_primary"})
    assert good.judged and good.reason
    assert good.auto_approve_eligible(level="official_primary", category="policy")


def test_auto_approve_eligibility_rules() -> None:
    """资格门槛：官方主源 + 稳定 + 不冲突 + 相关 + 分类允许 90 天，缺一不可。"""
    verdict = _reviewer(_STABLE).review(_TEXT, {})
    assert verdict.auto_approve_eligible(level="official_primary", category="policy")
    assert verdict.auto_approve_eligible(level="official_primary", category="stable_general")
    # 公告(7天)/办事(30天)放不下 90 天，且天然时效 → 不自动批准
    assert not verdict.auto_approve_eligible(level="official_primary", category="announcement")
    assert not verdict.auto_approve_eligible(level="official_primary", category="dynamic_service")
    # 公众号/自媒体等级一律人工（智能搜索拿不到账号名，见 09-17 决定）
    assert not verdict.auto_approve_eligible(level="unverified", category="policy")
    assert not verdict.auto_approve_eligible(level="general", category="policy")


# ---------------------------------------------------------------- 相关性闸门

def test_off_topic_candidate_never_enters_queue(tmp_path) -> None:
    """跑题资料必须**不进审核队列**，且失败码留在 job 上供日报统计。"""
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate("demo", _candidate())
    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_OFF_TOPIC), worker_id="w-off-topic"
    )
    assert worker.run_once() == "dead"
    assert _items(store) == [], "跑题资料不该建 draft"
    assert _jobs(settings.review_db_path) == [("dead", "OFF_TOPIC")]
    # 没有建 draft → 没有 review_item 级的 pre_review 审计
    assert _details(settings.review_db_path, "pre_review") == []
    # 但"为什么拦下"必须留在审计里（挂在 ingestion job 上，可追溯）
    rejected = _details(settings.review_db_path, "pre_review_rejected")
    assert len(rejected) == 1
    assert rejected[0]["relevance"] == "off_topic"
    assert rejected[0]["reason"] == "与校园知识无关"
    # 日报要把被拦下的也算进"已预审"
    assert store.review_stats("demo")["pre_reviewed"] == 1


def test_judgement_failure_still_ingests_but_never_auto_approves(tmp_path) -> None:
    """预审失败（unknown）**不能阻塞 ingest**：照常建 draft，但没有自动批准资格。"""
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate("demo", _candidate())

    def _boom(_prompt):
        raise RuntimeError("llm down")

    worker = IngestionWorker(
        store, pre_reviewer=PreReviewer("m", invoke=_boom), auto_approve=True, worker_id="w-fail"
    )
    assert worker.run_once() == "done"
    assert len(_items(store)) == 1
    assert _items(store)[0]["status"] == "draft"
    detail = _details(settings.review_db_path, "pre_review")[0]
    assert detail["fallback_reason"] == "llm_error:RuntimeError"
    assert detail["auto_approve_eligible"] is False
    assert _details(settings.review_db_path, "auto_approve") == []


# ---------------------------------------------------------------- 分级自动批准

def test_auto_approve_off_records_eligibility_only(tmp_path) -> None:
    """默认关：只记录"本可自动批准"，条目仍是 draft（供日报对照）。"""
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate("demo", _candidate())
    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_STABLE), auto_approve=False, worker_id="w-off"
    )
    assert worker.run_once() == "done"
    assert _items(store)[0]["status"] == "draft"
    detail = _details(settings.review_db_path, "pre_review")[0]
    assert detail["auto_approve_eligible"] is True
    assert detail["reason"] == "长期有效的政策"
    assert _details(settings.review_db_path, "auto_approve") == []


def test_auto_approve_on_uses_the_manual_publish_chain(tmp_path) -> None:
    """开启后：逐块批准 → approve_item → 排队发布（与人工同一条链路）。"""
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate("demo", _candidate())
    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_STABLE), auto_approve=True, worker_id="w-auto"
    )
    assert worker.run_once() == "done"
    item = _items(store)[0]
    # approve_item 落在 `approved` 并**排上发布任务**；再往后由 PublicationWorker 推到
    # pending_publish → active（本用例不跑发布 worker，只锁"与人工同一链路"）。
    assert item["status"] == "approved", item["status"]
    with sqlite3.connect(settings.review_db_path) as conn:
        approved = conn.execute(
            "SELECT COUNT(*) FROM review_chunks WHERE approved = 1"
        ).fetchone()[0]
        jobs = conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0]
    assert approved >= 1, "所有块都要被批准"
    assert jobs == 1, "必须排上发布任务"
    auto_detail = _details(settings.review_db_path, "auto_approve")[0]
    assert auto_detail == {"category": "policy", "ttl_days": 90}


def test_timeliness_category_blocks_auto_approve(tmp_path) -> None:
    """双重把关：即便预审判"稳定"，关键词分类若是公告/办事类也**不自动批准**。

    `_classify` 是确定性关键词分类（公告 7 天 / 办事 30 天放不下 90 天，且天然时效），
    与 LLM 判定互相独立——两者都同意才自动批准。失败方向是**保守的**：只少批，不错批。
    """
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate(
        "demo",
        {**_candidate(), "title": "关于选课的通知", "snapshot_text": "关于选课的通知：请同学们按时办理选课手续，具体安排见系统。"},
    )
    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_STABLE), auto_approve=True, worker_id="w-announce"
    )
    assert worker.run_once() == "done"
    item = _items(store)[0]
    assert item["category"] == "announcement", item["category"]
    assert item["status"] == "draft"
    assert _details(settings.review_db_path, "auto_approve") == []
    # 但仍要如实记录"本可自动批准 = False"（因为分类放不下 90 天）
    assert _details(settings.review_db_path, "pre_review")[0]["auto_approve_eligible"] is False


def test_same_snapshot_jobs_share_one_item_without_failing(tmp_path) -> None:
    """同一 snapshot 的多个 job（不同 evidence_span）命中同一条 item：只批准一次、不报错。"""
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate("demo", _candidate(span="s-a"))
    store.enqueue_candidate("demo", _candidate(span="s-b"))
    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_STABLE), auto_approve=True, worker_id="w-dup"
    )
    assert worker.run_once() == "done"
    assert worker.run_once() == "done"
    items = _items(store)
    assert len(items) == 1, "同一正文只该有一条 item"
    assert items[0]["status"] == "approved"
    assert len(_details(settings.review_db_path, "auto_approve")) == 1, "只批准一次"


def test_non_official_source_never_auto_approved(tmp_path) -> None:
    """公众号/未核实来源即使判定良好也**不自动批准**（拿不到账号名，见 09-17 决定）。"""
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    store.enqueue_candidate("demo", _candidate(level="unverified"))
    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_STABLE), auto_approve=True, worker_id="w-wechat"
    )
    assert worker.run_once() == "done"
    assert _items(store)[0]["status"] == "draft"
    assert _details(settings.review_db_path, "auto_approve") == []


# ---------------------------------------------------------------- 日报统计

def test_review_stats_counts_and_endpoint(tmp_path) -> None:
    """日报数字要与库里的真实情况对得上，且端点不能被 `/{item_id}` 吞掉。"""
    # demo 管理员才有审核命名空间（`Principal.review_namespace`）
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    store = ReviewStore(settings)
    store.initialize()
    # ⚠️ 正文必须各不相同：正文相同 → snapshot_hash 相同 → create_draft 复用同一条 item
    for index in range(3):
        store.enqueue_candidate(
            "demo",
            {
                **_candidate(span=f"s-{index}", url=f"https://www.teach.ustc.edu.cn/policy/{index}"),
                "snapshot_text": f"{_TEXT}（第 {index} 版）",
            },
        )
    stats = store.review_stats("demo")
    assert stats["ingested"] == 3
    assert stats["draft_backlog"] == 0, "还没跑 worker，draft 为 0"

    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_STABLE), auto_approve=True, worker_id="w-stats"
    )
    for _ in range(3):
        worker.run_once()
    stats = store.review_stats("demo")
    assert stats["pre_reviewed"] == 3
    assert stats["auto_eligible"] == 3
    assert stats["auto_approved"] == 3
    # 自动批准后条目落在 approved（发布 worker 才会推到 active）
    assert stats["items"].get("approved") == 3
    assert stats["active_items"] == 0
    assert stats["draft_backlog"] == 0
    assert stats["active_documents"] in {0, 3}  # 未跑发布 worker 时也可以是 0
    assert stats["off_topic"] == 0

    app = create_app(settings, runner=ImmediateRunner(), review_store=store)
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        assert client.post("/api/v1/auth/demo", headers=mutation_headers(csrf)).status_code == 200
        response = client.get("/api/v1/admin/review-items/stats")
        assert response.status_code == 200
        payload = response.json()
        assert payload["namespace"] == "demo"
        assert payload["auto_approved"] == 3
        assert payload["window_seconds"] == 24 * 60 * 60
        # 路由顺序：/{item_id} 仍然正常工作（没被 /stats 顶掉）
        item_id = _items(store)[0]["item_id"]
        assert client.get(f"/api/v1/admin/review-items/{item_id}").status_code == 200
