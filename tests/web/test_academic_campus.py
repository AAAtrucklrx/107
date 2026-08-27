"""Academic identity binding and public campus source labelling."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.academic import AcademicService
from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError
from xiaowo_web.main import create_app


def test_anonymous_cannot_read_academic_workspace(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=ImmediateRunner())
    with TestClient(app) as client:
        bootstrap(client)
        response = client.get("/api/v1/academic/overview")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_demo_academic_workspace_is_bound_and_labelled(tmp_path) -> None:
    app = create_app(make_settings(tmp_path, mode="demo"), runner=ImmediateRunner())
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        session = client.post("/api/v1/auth/demo", headers=mutation_headers(csrf)).json()
        assert session["principal"]["profile"]["id"] == "PB25111691"

        overview = client.get("/api/v1/academic/overview").json()
        assert overview["identity"]["major"] == "人工智能"
        assert overview["identity"]["grade"] == "2025级"
        assert overview["source"]["demo"] is True
        assert overview["metrics"]["grade_count"] > 0

        program = client.get("/api/v1/academic/program").json()
        assert program["program"]["source"] == "demo_personal"
        assert program["source"]["label"] == "合成演示数据"
        assert "演示数据" in program["banner"]

        schedule = client.get("/api/v1/academic/schedule").json()
        assert schedule["courses"]
        assert schedule["source"]["demo"] is True


def test_cas_profile_mismatch_or_missing_fields_never_guess() -> None:
    service = AcademicService()
    mismatch = Principal(
        principal_id="PB25111691",
        auth_mode="cas",
        profile={"id": "PB00000000", "major": "人工智能", "grade": "2025级"},
        is_admin=False,
        session_key="session",
    )
    with pytest.raises(ApiError) as mismatch_error:
        service.overview(mismatch)
    assert mismatch_error.value.code == "PROFILE_ID_MISMATCH"

    incomplete = Principal(
        principal_id="PB25111691",
        auth_mode="cas",
        profile={"id": "PB25111691", "name": "测试"},
        is_admin=False,
        session_key="session",
    )
    with pytest.raises(ApiError) as incomplete_error:
        service.overview(incomplete)
    assert incomplete_error.value.code == "PROFILE_INCOMPLETE"


def test_campus_services_are_public_and_curated(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=ImmediateRunner())
    with TestClient(app) as client:
        response = client.get("/api/v1/campus/services", params={"query": "图书馆"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["source"]["kind"] == "curated_config"
        assert len(payload["items"]) == 1
        assert payload["items"][0]["url"] == "https://lib.ustc.edu.cn"
