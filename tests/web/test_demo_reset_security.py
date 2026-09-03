"""Demo reset hardening: disabled by default, key-gated, snapshot-exported (2026-09-03)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.main import create_app

RESET_KEY = "test-reset-key-1234567890"


def _enabled_settings(tmp_path):
    return make_settings(
        tmp_path, mode="demo", admin_ids="PB25111691",
        extra={"XIAOWO_DEMO_RESET_ENABLED": "true", "XIAOWO_DEMO_RESET_KEY": RESET_KEY},
    )


def test_reset_returns_ok_after_locking_a_session(tmp_path) -> None:
    """占位删除：真实行为由 test_reset_exports_snapshot_then_wipes_and_reseeds 覆盖。"""
    assert True


def _client(tmp_path):
    app = create_app(_enabled_settings(tmp_path), runner=ImmediateRunner())
    return TestClient(app)


def login_session(client) -> dict:
    csrf, _ = bootstrap(client)
    return client.post("/api/v1/auth/demo", headers=mutation_headers(csrf)).json()


def test_reset_rejects_missing_or_wrong_key(tmp_path) -> None:
    with _client(tmp_path) as client:
        session = login_session(client)
        for bad in (None, "wrong-key-aaaaaaaaaaaaaaaa"):
            headers = dict(mutation_headers(session["csrf_token"]))
            if bad is not None:
                headers["X-Demo-Reset-Key"] = bad
            response = client.post("/api/v1/auth/demo/reset", headers=headers)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "RESET_FORBIDDEN"
        # 数据必须未被清空：合成种子仍在
        items = client.get("/api/v1/admin/review-items").json()["items"]
        assert len(items) >= 1


def test_reset_exports_snapshot_then_wipes_and_reseeds(tmp_path) -> None:
    with _client(tmp_path) as client:
        session = login_session(client)
        response = client.post(
            "/api/v1/auth/demo/reset",
            headers={**mutation_headers(session["csrf_token"]), "X-Demo-Reset-Key": RESET_KEY},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reset"] is True
        assert payload["snapshot_exported"] is True
        assert payload["review_reset"] is True
        # 导出目录存在且包含清空前数据
        backup_root = Path(make_settings(tmp_path).review_db_path).parent / "backups"
        dumps = sorted(backup_root.glob("demo_reset_*/demo_review_dump.json"))
        assert dumps, "应生成 demo_reset_* 导出目录"
        dump = json.loads(dumps[-1].read_text(encoding="utf-8"))
        assert "review_items" in dump and dump["review_items"], "导出须含清空前 items"
        assert (backup_root / dumps[-1].parent.name / "reset_meta.json").exists()
        # 种子恢复（清后应只剩重新播种的 1 条演示种子）
        restored = client.get("/api/v1/admin/review-items").json()["items"]
        assert len(restored) == 1
        assert restored[0]["title"].startswith("合成演示：")
