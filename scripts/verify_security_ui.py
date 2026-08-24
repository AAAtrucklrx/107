"""认证边界、个人数据工具与 Streamlit 重构回归。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES: list[str] = []
TOTAL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


class _RecorderTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"ok": True}


def verify_agent_identity_binding() -> None:
    from agents.qa import nodes

    recorder = _RecorderTool()
    original_registry = nodes._build_tool_registry
    nodes._build_tool_registry = lambda: {"query_grade": recorder}
    try:
        nodes.act({
            "decision": "call_tool",
            "rounds": 0,
            "student_id": "PB-AUTH",
            "tool_calls": [{"tool": "query_grade", "args": {"student_id": "PB-VICTIM"}}],
            "tool_results": [],
        })
        check(
            "Agent 个人工具绑定认证学号",
            recorder.calls[-1]["student_id"] == "PB-AUTH",
            str(recorder.calls[-1]),
        )

        nodes.act({
            "decision": "call_tool",
            "rounds": 0,
            "student_id": "",
            "tool_calls": [{"tool": "query_grade", "args": {"student_id": "PB-VICTIM"}}],
            "tool_results": [],
        })
        check(
            "未登录时清除模型伪造学号",
            recorder.calls[-1]["student_id"] == "",
            str(recorder.calls[-1]),
        )
    finally:
        nodes._build_tool_registry = original_registry


def verify_ticket_and_logout_isolation() -> None:
    from services.service_container import ServiceContainer
    from services.session_ctx import reset_student, set_student

    class FakeTicketClient:
        def __init__(self) -> None:
            self.student_id = "PB-CALLBACK"
            self.is_logged_in = True
            self.logged_out = False

        def login_with_ticket(self, ticket, service_url=None) -> bool:
            return ticket == "ST-OK"

        def logout(self) -> None:
            self.logged_out = True
            self.is_logged_in = False

    ServiceContainer.reset()
    with patch("services.cas_client.CASClient", FakeTicketClient):
        container = ServiceContainer()
        client = container.authenticate_ticket("ST-OK")
        token = set_student("PB-CALLBACK")
        try:
            bucket_client = container.cas_client
        finally:
            reset_student(token)
        check(
            "ticket 回调返回并归档同一客户端",
            client is not None and bucket_client is client,
        )
    ServiceContainer.reset()

    container = ServiceContainer()
    token_a = set_student("PB-A")
    client_a = container.cas_client
    client_a._logged_in = True
    client_a._student_id = "PB-A"
    reset_student(token_a)

    token_b = set_student("PB-B")
    client_b = container.cas_client
    client_b._logged_in = True
    client_b._student_id = "PB-B"
    reset_student(token_b)

    container.logout("PB-A")
    token_a = set_student("PB-A")
    a_logged_in = container.has_cas()
    reset_student(token_a)
    token_b = set_student("PB-B")
    b_logged_in = container.has_cas()
    reset_student(token_b)
    check(
        "登出只清理当前学生桶",
        not a_logged_in and b_logged_in and ServiceContainer() is container,
    )
    ServiceContainer.reset()


def verify_cas_final_host() -> None:
    from services.cas_client import CASClient

    class Response:
        status_code = 200
        url = "https://id.ustc.edu.cn/cas/login?service=jw"
        text = "CAS login"

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    client = CASClient()
    client.validate_ticket = lambda ticket, service_url=None: (True, "PB-AUTH")
    client._session = Session()
    result = client.login_with_ticket("ST-OK")
    check("CAS 登录页 200 不得冒充教务登录成功", result is False and not client.is_logged_in)


def verify_ui_and_entrypoint() -> None:
    from ui.chat import _render_card

    card = _render_card({
        "type": "check_course_conflict",
        "data": {
            "total": 0,
            "conflicts": [],
            "missing": ["量子力学"],
            "message": "未找到这些课程的选课/课表数据",
        },
    }) or ""
    check(
        "缺少排课数据时不显示无冲突",
        "无冲突" not in card and "未找到" in card,
        card.replace("\n", " "),
    )

    import app
    import app_test

    check("测试入口复用正式 main", app_test.main is app.main)
    sources = [
        (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")
        for rel in ("ui/chat.py", "ui/program_page.py")
    ]
    check("Streamlit 弃用宽度参数已清理", all("use_container_width" not in src for src in sources))


def verify_vector_store_preserves_data() -> None:
    from knowledge import vector_store

    with (
        TemporaryDirectory() as tmp_dir,
        patch.object(vector_store.chromadb, "PersistentClient", side_effect=OSError("locked")),
        patch.object(vector_store, "_nuke_chroma_db") as nuke,
    ):
        message = ""
        try:
            vector_store.FAQVectorStore(tmp_dir)
        except RuntimeError as exc:
            message = str(exc)
        check(
            "向量库初始化失败保留原索引",
            not nuke.called and "rebuild_kb.py --yes" in message,
            message,
        )


def verify_course_tools() -> None:
    from services.service_container import ServiceContainer
    from tools import course_tools

    ServiceContainer.reset()
    daily = course_tools.query_daily_schedule.invoke({"student_id": ""})
    check(
        "每日课表缺省日期可调用",
        daily.get("date") == datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        and daily.get("source") == "locked",
        str(daily),
    )

    class Catalog:
        @staticmethod
        def get_current_semester():
            return {"id": 1, "nameZh": "测试学期"}

        @staticmethod
        def get_exams(semester_id):
            return [
                {
                    "lesson": {"course": {"cn": "数学分析", "code": "MATH100"}},
                    "examDate": "2026-09-01",
                    "startTime": 900,
                    "endTime": 1100,
                },
                {
                    "lesson": {"course": {"cn": "他人课程", "code": "OTHER999"}},
                    "examDate": "2026-09-02",
                    "startTime": 900,
                    "endTime": 1100,
                },
            ]

        @staticmethod
        def get_general_exams(semester_id):
            return []

    with (
        patch.object(course_tools, "_is_locked", return_value=False),
        patch.object(course_tools, "_catalog", return_value=Catalog()),
        patch.object(course_tools, "_cas", return_value=object()),
        patch.object(
            course_tools,
            "_fetch_real_schedule",
            return_value=[{"course_code": "MATH100", "course_name": "数学分析"}],
        ),
    ):
        exams = course_tools.query_exam.invoke({"student_id": "PB-AUTH"})
    check(
        "考试查询过滤全校数据",
        exams.get("count") == 1 and exams["exams"][0]["course_code"] == "MATH100",
        str(exams),
    )
    ServiceContainer.reset()


def verify_morning_brief_context() -> None:
    from services import morning_brief
    from services.session_ctx import current_student

    seen: list[str] = []

    def builder(_student_id: str):
        seen.append(current_student())

    with (
        patch.object(morning_brief, "_build_schedule", builder),
        patch.object(morning_brief, "_build_exam_today", builder),
        patch.object(morning_brief, "_build_ddl", builder),
    ):
        morning_brief.build_morning_brief("PB-BRIEF")
    check(
        "晨报在学生上下文中聚合",
        seen == ["PB-BRIEF"] * 3 and current_student() == "",
        str(seen),
    )


def verify_schedule_week_semantics() -> None:
    from database.db_manager import DatabaseManager
    from tools import course_tools, schedule_tools
    from utils.schedule_parse import parse_course_time, slots_overlap

    odd = parse_course_time("周一 1~8(单)周 第1,2节")[0]
    even = parse_course_time("周一 2~8(双)周 第1,2节")[0]
    verdict, _ = slots_overlap(odd, even)
    parsed_group = course_tools._parse_schedule_group_str(
        "2,6~18(双)周 2306 :3(1,2) 孙波"
    )
    check(
        "不连续与单双周解析贯穿教务课表",
        odd["week_numbers"] == [1, 3, 5, 7]
        and even["week_numbers"] == [2, 4, 6, 8]
        and verdict == "no_conflict"
        and parsed_group["weeks"] == "2,6~18(双)周"
        and parsed_group["location"] == "2306",
        str((odd, even, verdict, parsed_group)),
    )

    semester = {"name": "test", "start_date": "2026-08-31", "total_weeks": 8}
    courses = [
        {
            "course_name": "范围课",
            "time": "周一 2~3周 第1,2节",
            "location": "101",
        },
        {
            "course_name": "离散双周课",
            "time": "周二 第3,4节",
            "location": "2,6~8(双)周 202",
        },
        {"course_name": "无时间课", "time": "", "location": ""},
    ]

    class StaticScheduleTool:
        @staticmethod
        def invoke(_args: dict) -> dict:
            return {"courses": courses, "source": "fallback"}

    with TemporaryDirectory() as tmp_dir:
        db = DatabaseManager(Path(tmp_dir) / "schedule.db")
        db.init_schema(Path(__file__).resolve().parents[1] / "database" / "schema.sql")
        db.execute(
            """INSERT INTO events
               (student_id, title, event_type, start_time, end_time,
                is_recurring, source)
               VALUES (?, ?, 'course', ?, ?, 1, 'schedule_import')""",
            ("PB-WEEK", "旧课表", "2026-08-31T08:00:00", "2026-08-31T09:35:00"),
        )
        db.execute(
            """INSERT INTO events
               (student_id, title, event_type, start_time, end_time, source)
               VALUES (?, ?, 'custom', ?, ?, 'manual')""",
            ("PB-WEEK", "手工日程", "2026-09-01T12:00:00", "2026-09-01T13:00:00"),
        )

        with (
            patch("config.SEMESTER", semester),
            patch.object(schedule_tools, "_db", return_value=db),
            patch.object(course_tools, "query_schedule", StaticScheduleTool()),
        ):
            first = schedule_tools.import_schedule.invoke({"student_id": "PB-WEEK"})
            second = schedule_tools.import_schedule.invoke({"student_id": "PB-WEEK"})

        imported_events = db.query(
            """SELECT title, start_time, is_recurring FROM events
               WHERE student_id=? AND source='schedule_import'
               ORDER BY start_time""",
            ("PB-WEEK",),
        )
        imported_dates = [event["start_time"][:10] for event in imported_events]
        manual_count = db.query_one(
            "SELECT COUNT(*) AS count FROM events WHERE source='manual'"
        )["count"]
        check(
            "课表按实际周次原子替换且重复导入幂等",
            first["event_count"] == 5
            and second["event_count"] == 5
            and len(imported_events) == 5
            and imported_dates == [
                "2026-09-07",
                "2026-09-08",
                "2026-09-14",
                "2026-10-06",
                "2026-10-20",
            ]
            and all(event["is_recurring"] == 0 for event in imported_events)
            and manual_count == 1
            and first["time_unparsed"] == ["无时间课(无时间)"],
            str((first, second, imported_events, manual_count)),
        )

        db.execute(
            """INSERT INTO student_courses
               (student_id, course_code, course_name, teacher, credits,
                time, location, semester)
               VALUES (?, ?, ?, '', 1, ?, '', 'test')""",
            ("PB-WEEK", "WEEK101", "范围课", "周一 2~3周 第1,2节"),
        )
        with (
            patch("config.SEMESTER", semester),
            patch.object(course_tools, "_db", return_value=db),
            patch.object(course_tools, "_is_locked", return_value=False),
            patch.object(course_tools, "_cas", return_value=None),
        ):
            week_one = course_tools.query_daily_schedule.invoke({
                "student_id": "PB-WEEK",
                "date": "2026-08-31",
            })
            week_two = course_tools.query_daily_schedule.invoke({
                "student_id": "PB-WEEK",
                "date": "2026-09-07",
            })
            outside = course_tools.query_daily_schedule.invoke({
                "student_id": "PB-WEEK",
                "date": "2026-10-26",
            })
        check(
            "每日课表按教学周过滤并拒绝学期外日期",
            week_one["count"] == 0
            and week_two["count"] == 1
            and week_two["teaching_week"] == 2
            and outside["source"] == "calendar"
            and outside["count"] == 0,
            str((week_one, week_two, outside)),
        )
        db.close()


def main() -> None:
    verify_agent_identity_binding()
    verify_ticket_and_logout_isolation()
    verify_cas_final_host()
    verify_ui_and_entrypoint()
    verify_vector_store_preserves_data()
    verify_course_tools()
    verify_morning_brief_context()
    verify_schedule_week_semantics()
    passed = TOTAL - len(FAILURES)
    print(f"\n结果: 通过 {passed}/{TOTAL}")


if __name__ == "__main__":
    main()
    raise SystemExit(1 if FAILURES else 0)
