"""意图落库（2026-09-29）：意图此前完全不落库，线上无法统计分布与做回归。

纯本地嵌入分类 + SQLite 写列，不调用 LLM；埋点失败不得影响答题。
"""
from __future__ import annotations

import json
import sqlite3
import time

from tests.web.helpers import make_settings
from xiaowo_web.storage.web_store import WebStore


def _row(settings, run_id: str) -> dict:
    conn = sqlite3.connect(settings.app_db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM web_chat_runs WHERE run_id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


def test_run_table_has_intent_columns(tmp_path):
    store = WebStore(make_settings(tmp_path))
    store.initialize()
    run = store.create_run("owner-1", "auto")
    row = _row(store.settings, run.run_id)
    assert {"intent", "intent_score", "intent_top3"} <= set(row)


def test_set_run_intent_persists_and_json_roundtrip(tmp_path):
    store = WebStore(make_settings(tmp_path))
    store.initialize()
    run = store.create_run("owner-1", "auto")
    top3 = [{"intent": "查课表", "score": 1.0}, {"intent": "课程搜索", "score": 0.78}]
    store.set_run_intent(run.run_id, "查课表", 1.0, top3)
    row = _row(store.settings, run.run_id)
    assert row["intent"] == "查课表"
    assert abs(row["intent_score"] - 1.0) < 1e-6
    assert json.loads(row["intent_top3"]) == top3


def test_set_run_intent_tolerates_empty(tmp_path):
    store = WebStore(make_settings(tmp_path))
    store.initialize()
    run = store.create_run("owner-1", "auto")
    store.set_run_intent(run.run_id, "", None, [])
    row = _row(store.settings, run.run_id)
    assert row["intent"] is None and row["intent_score"] is None and row["intent_top3"] is None


def test_migration_adds_columns_to_legacy_db(tmp_path):
    """老库（无意图列）在 initialize() 时就地补列，不丢数据。"""
    settings = make_settings(tmp_path)
    conn = sqlite3.connect(settings.app_db_path)
    conn.execute(
        "CREATE TABLE web_chat_runs (run_id TEXT PRIMARY KEY, owner_key TEXT NOT NULL, mode TEXT NOT NULL,"
        " status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,"
        " cancel_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT)"
    )
    # expires_at 必须给未来时间：initialize() 内的 prune_expired 会清掉过期运行
    conn.execute("INSERT INTO web_chat_runs VALUES ('legacy', 'o', 'auto', 'completed', 1, 1, ?, 0, NULL)",
                 (time.time() + 3600,))
    conn.commit(); conn.close()

    store = WebStore(settings)
    store.initialize()  # 触发迁移
    store.set_run_intent("legacy", "闲聊", 0.9, [{"intent": "闲聊", "score": 0.9}])
    row = _row(settings, "legacy")
    assert row["intent"] == "闲聊" and row["owner_key"] == "o"  # 老数据仍在


def test_intent_recorded_end_to_end(tmp_path):
    """端到端：runner 返回意图 → manager 落库（这是埋点唯一可能断掉的连接处）。"""
    import dataclasses
    import time

    from fastapi.testclient import TestClient
    from tests.web.helpers import ImmediateRunner, bootstrap, mutation_headers
    from xiaowo_web.main import create_app

    class IntentRunner(ImmediateRunner):
        async def run(self, request):
            bundle = await super().run(request)
            return dataclasses.replace(
                bundle, intent="查课表", intent_score=1.0,
                intent_top3=[{"intent": "查课表", "score": 1.0}],
            )

    settings = make_settings(tmp_path)
    app = create_app(settings, runner=IntentRunner())
    with TestClient(app) as client:
        csrf, _session = bootstrap(client)
        resp = client.post(
            "/api/v1/chat/runs",
            json={"question": "科大有哪些校园服务？", "mode": "local"},
            headers=mutation_headers(csrf),
        )
        assert resp.status_code in (200, 201, 202), resp.text
        run_id = resp.json()["run_id"]

        row = {}
        for _ in range(50):
            row = _row(settings, run_id) or {}
            if row.get("intent"):
                break
            time.sleep(0.05)

    assert row.get("intent") == "查课表"
    assert abs((row.get("intent_score") or 0) - 1.0) < 1e-6
    assert json.loads(row["intent_top3"])[0]["intent"] == "查课表"
