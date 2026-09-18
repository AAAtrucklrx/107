"""自动批准「运行时开关」（2026-09-18）。

原本只有 `.env` 的 `XIAOWO_REVIEW_AUTO_APPROVE`：改一次要重启 worker，后台既看不到
也改不了。现在把开关做成**库里的运行时设置 + 审计**，worker 每个 job 现读 → 后台即时开关。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from tests.web.test_pre_review import _STABLE, _candidate, _details, _items, _reviewer
from xiaowo_web.main import create_app
from xiaowo_web.review import ReviewStore
from xiaowo_web.review.store import AUTO_APPROVE_SETTING_KEY
from xiaowo_web.worker import IngestionWorker


def _store(tmp_path):
    settings = make_settings(tmp_path)
    store = ReviewStore(settings)
    store.initialize()
    return store, settings


def test_runtime_flag_round_trip_and_audit(tmp_path) -> None:
    """开关能存能读，且**留痕**（谁在什么时候从什么改到什么）；命名空间互相隔离。"""
    store, settings = _store(tmp_path)
    assert store.get_runtime_flag("demo", AUTO_APPROVE_SETTING_KEY, default=False) is False
    assert store.runtime_flag_state("demo", AUTO_APPROVE_SETTING_KEY) is None

    store.set_runtime_flag(
        "demo", AUTO_APPROVE_SETTING_KEY, True, actor_key="tester", request_id="req-1"
    )
    assert store.get_runtime_flag("demo", AUTO_APPROVE_SETTING_KEY, default=False) is True
    state = store.runtime_flag_state("demo", AUTO_APPROVE_SETTING_KEY)
    assert state is not None and state["value"] is True and state["updated_by"] == "tester"

    changed = _details(settings.review_db_path, "runtime_flag_changed")
    assert len(changed) == 1
    assert changed[0]["key"] == AUTO_APPROVE_SETTING_KEY
    assert changed[0]["before"] is None and changed[0]["after"] is True

    # 命名空间隔离：production 不受 demo 的改动影响
    assert store.get_runtime_flag("production", AUTO_APPROVE_SETTING_KEY, default=False) is False


def test_worker_reads_the_switch_on_every_job(tmp_path) -> None:
    """同一个 worker：先把开关关着跑一次（draft），打开开关再跑一次（自动批准）—— 无需重启。"""
    store, settings = _store(tmp_path)
    worker = IngestionWorker(
        store,
        pre_reviewer=_reviewer(_STABLE),
        auto_approve=lambda namespace: store.get_runtime_flag(
            namespace, AUTO_APPROVE_SETTING_KEY, default=False
        ),
        worker_id="w-runtime",
    )

    store.enqueue_candidate(
        "demo", _candidate(span="span-1", url="https://www.teach.ustc.edu.cn/policy/1")
    )
    assert worker.run_once() == "done"
    assert _items(store)[0]["status"] == "draft", "开关关着时只能记资格，不能自动批准"
    assert _details(settings.review_db_path, "auto_approve") == []

    store.set_runtime_flag(
        "demo", AUTO_APPROVE_SETTING_KEY, True, actor_key="tester", request_id="req-2"
    )
    store.enqueue_candidate(
        "demo", _candidate(span="span-2", url="https://www.teach.ustc.edu.cn/policy/2")
    )
    assert worker.run_once() == "done"
    assert len(_details(settings.review_db_path, "auto_approve")) == 1, "开关打开后应自动批准"
    assert any(item["status"] != "draft" for item in _items(store)), [
        item["status"] for item in _items(store)
    ]


def test_worker_fails_closed_when_flag_read_raises(tmp_path) -> None:
    """读设置失败一律按关闭处理：绝不因为读不到开关而自动发布。"""
    store, settings = _store(tmp_path)

    def _boom(_namespace: str) -> bool:
        raise RuntimeError("db locked")

    worker = IngestionWorker(
        store, pre_reviewer=_reviewer(_STABLE), auto_approve=_boom, worker_id="w-boom"
    )
    store.enqueue_candidate("demo", _candidate(span="span-x"))
    assert worker.run_once() == "done"
    assert _items(store)[0]["status"] == "draft"
    assert _details(settings.review_db_path, "auto_approve") == []


def test_settings_api_get_patch_and_stats(tmp_path) -> None:
    """后台接口：看状态 → 打开 → 日报同步 → 关回去。"""
    settings = make_settings(tmp_path, mode="demo", admin_ids="PB25111691")
    app = create_app(settings, runner=ImmediateRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        session = client.post("/api/v1/auth/demo", headers=mutation_headers(csrf)).json()
        csrf = session["csrf_token"]

        state = client.get("/api/v1/admin/review-items/settings").json()["auto_approve"]
        assert state["enabled"] is False
        assert state["default"] is False
        assert state["override"] is False, "没设置过时应显示沿用 .env 默认"

        patched = client.patch(
            "/api/v1/admin/review-items/settings",
            json={"auto_approve": True},
            headers=mutation_headers(csrf),
        )
        assert patched.status_code == 200, patched.text
        state = patched.json()["auto_approve"]
        assert state["enabled"] is True
        assert state["override"] is True
        assert state["updated_by"] == "PB25111691"

        stats = client.get("/api/v1/admin/review-items/stats").json()
        assert stats["auto_approve"]["enabled"] is True

        back = client.patch(
            "/api/v1/admin/review-items/settings",
            json={"auto_approve": False},
            headers=mutation_headers(csrf),
        )
        assert back.json()["auto_approve"]["enabled"] is False
