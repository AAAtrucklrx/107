"""Academic identity binding and public campus source labelling."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.web.helpers import ImmediateRunner, bootstrap, make_settings, mutation_headers
from xiaowo_web.academic import AcademicService
from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError
from xiaowo_web.main import create_app
from tools.course_tools import _parse_schedule_groups
from utils.schedule_parse import normalize_time_str, parse_course_time


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
        assert overview["identity"]["major"] == "计算机科学与技术"
        assert overview["identity"]["grade"] == "2025级"
        assert overview["source"]["demo"] is True
        assert overview["metrics"]["grade_count"] > 0

        program = client.get("/api/v1/academic/program").json()
        assert program["program"]["source"] in ("personal", "demo_personal", "generic")
        assert program["source"]["label"] == "合成演示数据"
        assert "演示数据" in program["banner"]

        schedule = client.get("/api/v1/academic/schedule").json()
        assert schedule["courses"]
        assert schedule["source"]["demo"] is True
        assert schedule["semester_code"] == "2026-2027-1"
        assert schedule["semester_start"] == "2026-08-31"
        assert schedule["total_weeks"] == 18
        assert schedule["current_week"] == 1
        assert schedule["unparsed_courses"] == []

        meetings = [
            meeting
            for course in schedule["courses"]
            for meeting in course["meetings"]
        ]
        assert {meeting["weekday"] for meeting in meetings} >= {1, 2, 3, 4, 5}
        assert any(meeting["periods"] == [1, 2] and meeting["start_time"] == "07:50" for meeting in meetings)
        assert any(meeting["periods"] == [3, 4, 5] and meeting["end_time"] == "12:10" for meeting in meetings)
        assert any(meeting["periods"] == [11, 12, 13] and meeting["end_time"] == "21:55" for meeting in meetings)
        evening = next(meeting for meeting in meetings if meeting["periods"] == [11, 12, 13])
        # 真实课表（2026-09-03 起数据源为本人教务课表）：晚间课为“大学物理-综合实验B”3~18 周
        assert evening["week_numbers"] == list(range(3, 19))


def test_real_schedule_groups_preserve_multiple_meetings_and_week_parity() -> None:
    parsed = _parse_schedule_groups(
        "2,6~18(双)周 2306 :3(1,2) 孙波\n"
        "1~16周 3C201 :1(11,12,13) 李明"
    )
    assert len(parsed) == 2
    assert parsed[0] == {
        "weeks": "2,6~18(双)周",
        "week_numbers": [2, 6, 8, 10, 12, 14, 16, 18],
        "location": "2306",
        "day_str": "周三",
        "day_num": 3,
        "periods": "1,2",
        "teacher_hint": "孙波",
        "raw": "2,6~18(双)周 2306 :3(1,2) 孙波",
    }
    assert parsed[1]["day_num"] == 1
    assert parsed[1]["periods"] == "11,12,13"
    assert parsed[1]["location"] == "3C201"


def test_course_time_ranges_are_normalized_without_losing_periods() -> None:
    samples = {
        "周二 1~16周 第3-5节 09:45-12:10": [3, 4, 5],
        "周四8—10节": [8, 9, 10],
        "周三 第11~13节": [11, 12, 13],
        "周五第1至2节": [1, 2],
    }
    for raw, expected in samples.items():
        normalized = normalize_time_str(raw)
        assert "第" in normalized
        assert parse_course_time(normalized)[0]["periods"] == expected


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
        assert payload["items"][0]["featured"] is True
        assert payload["items"][0]["priority"] == 4

        directory = client.get("/api/v1/campus/services").json()
        featured = sorted(
            (item for item in directory["items"] if item["featured"]),
            key=lambda item: item["priority"],
        )
        assert len(featured) == 8
        assert [item["priority"] for item in featured] == list(range(1, 9))
        assert directory["categories"] == [
            "教务学习",
            "交流升学",
            "个人事务",
            "生活服务",
            "就业发展",
        ]  # 社区工具分类已并入「校园工具」（学生共建体系，含预置精选）


def test_campus_activities_mapped_to_frontend_contract() -> None:
    """CampusService.activities 把工具字段（start/end/apply_end/place）映射为
    前端 CampusActivity 契约（start_time/end_time/deadline/location）。"""
    from xiaowo_web.campus.service import CampusService

    svc = CampusService(activity_provider=lambda **kw: {
        "activities": [{
            "id": "a1", "name": "开题讲座", "start": "2026-09-11 19:30:00",
            "end": "2026-09-11 20:30:00", "apply_end": "2026-09-11 17:00:00",
            "place": "3C301", "contact": "张三 123", "organizer": "学生社团",
            "category": "系列项目", "description": "讲座简介",
        }],
        "source": "实时数据（青春科大 young.ustc.edu.cn）",
        "fetched_at": "2026-09-02 22:00:00",
    })
    got = svc.activities()
    item = got["items"][0]
    assert item["title"] == "开题讲座"
    assert item["location"] == "3C301"
    assert item["start_time"] == "2026-09-11 19:30:00"
    assert item["end_time"] == "2026-09-11 20:30:00"
    assert item["deadline"] == "2026-09-11 17:00:00"
    assert item["contact"] == "张三 123"
    assert got["source"]["kind"] == "young_live"
    assert got["source"]["stale"] is False


def test_campus_activities_snapshot_is_stale_labelled() -> None:
    from xiaowo_web.campus.service import CampusService

    svc = CampusService(activity_provider=lambda **kw: {
        "activities": [{"name": "快照活动", "start": "2026-08-01 10:00:00",
                        "apply_end": "2026-07-31 23:59:00", "place": "",
                        "description": ""}],
        "source": "本地缓存（青春科大快照）——实时拉取失败",
        "fetched_at": "2026-08-01 00:00:00",
    })
    got = svc.activities()
    assert got["source"]["kind"] == "young_snapshot"
    assert got["source"]["stale"] is True
    assert any("快照" in lim for lim in got["limitations"])
    assert got["items"][0]["location"] == ""
