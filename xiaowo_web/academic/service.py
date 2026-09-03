"""Principal-bound facade over demo fixtures and existing academic tools."""

from __future__ import annotations

import json
import time
from datetime import date as Date
from pathlib import Path
from typing import Any

from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError
from xiaowo_web.settings import DEMO_STUDENT_ID, PROJECT_ROOT


DEMO_FIXTURE = PROJECT_ROOT / "fixtures" / "demo" / f"{DEMO_STUDENT_ID}.json"

# 教务官方教学周缓存：{student_id: (monotonic, data)}，TTL 30 分钟（教学周一天内不变）
_TEACH_WEEK_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TEACH_WEEK_TTL = 1800.0


class AcademicService:
    def _identity(self, principal: Principal) -> dict[str, Any]:
        if not principal.is_authenticated:
            raise ApiError(401, "AUTH_REQUIRED", "个人学业功能需要登录。")
        profile = dict(principal.profile)
        student_id = str(profile.get("id") or principal.principal_id).strip().upper()
        if student_id != principal.principal_id.strip().upper():
            raise ApiError(403, "PROFILE_ID_MISMATCH", "认证身份与学业档案不一致。")
        if not profile.get("major") or not profile.get("grade"):
            raise ApiError(409, "PROFILE_INCOMPLETE", "当前认证档案缺少专业或年级，无法加载培养方案。")
        return profile

    @staticmethod
    def _load_demo() -> dict[str, Any]:
        try:
            payload = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApiError(503, "DEMO_FIXTURE_UNAVAILABLE", "演示学业数据暂不可用。") from exc
        user = payload.get("user") or {}
        if not payload.get("synthetic") or str(user.get("id", "")).upper() != DEMO_STUDENT_ID:
            raise ApiError(503, "DEMO_FIXTURE_INVALID", "演示学业数据校验失败。")
        return payload

    def overview(self, principal: Principal) -> dict[str, Any]:
        profile = self._identity(principal)
        if principal.auth_mode == "demo":
            fixture = self._load_demo()
            grades = fixture.get("grades") or []
            acad = fixture.get("academic_overview") or {}
            if acad.get("gpa") is not None:
                # 教务口径优先(与成绩单一致);无则按成绩加权计算
                metrics = {
                    "gpa": float(acad["gpa"]),
                    "completed_credits": float(acad.get("passed_credits") or 0),
                    "current_credits": round(sum(float(row.get("credits") or 0) for row in fixture.get("courses") or []), 1),
                    "grade_count": len(grades),
                }
            else:
                weighted = sum(float(row.get("grade_point") or 0) * float(row.get("credits") or 0) for row in grades)
                total_credits = sum(float(row.get("credits") or 0) for row in grades)
                metrics = {
                    "gpa": round(weighted / total_credits, 2) if total_credits else None,
                    "completed_credits": round(total_credits, 1),
                    "current_credits": round(sum(float(row.get("credits") or 0) for row in fixture.get("courses") or []), 1),
                    "grade_count": len(grades),
                }
            return {
                "identity": profile,
                "metrics": metrics,
                "recent_grades": grades[-5:][::-1],
                "grades": grades[::-1],
                "source": self._demo_source(),
                "limitations": ["所有个人数据均为合成演示数据，不代表真实教务记录。"],
            }
        return self._cas_overview(principal, profile)

    def courses(self, principal: Principal) -> dict[str, Any]:
        self._identity(principal)
        if principal.auth_mode == "demo":
            fixture = self._load_demo()
            return {
                "courses": fixture.get("courses") or [],
                "grades": fixture.get("grades") or [],
                "source": self._demo_source(),
                "limitations": ["演示课程与成绩为合成数据。"],
            }
        from tools.course_tools import query_grade, query_schedule

        with self._student_context(principal.principal_id):
            schedule = query_schedule.invoke({"student_id": principal.principal_id})
            grades = query_grade.invoke({"student_id": principal.principal_id})
        return {
            "courses": schedule.get("courses") or [],
            "grades": grades.get("grades") or [],
            "source": self._combined_source(schedule, grades),
            "limitations": [value for value in (schedule.get("message"), grades.get("message")) if value],
        }

    def schedule(self, principal: Principal) -> dict[str, Any]:
        self._identity(principal)
        if principal.auth_mode == "demo":
            fixture = self._load_demo()
            result = self._structured_schedule(fixture.get("courses") or [])
            return {
                **result,
                "source": self._demo_source(),
                "limitations": ["演示课表为合成数据，不用于真实到课判断。"],
            }
        from tools.course_tools import query_schedule

        with self._student_context(principal.principal_id):
            result = query_schedule.invoke({"student_id": principal.principal_id})
        structured = self._structured_schedule(
            result.get("courses") or [],
            semester=str(result.get("semester") or ""),
        )
        teach_week = self._official_teach_week(principal)
        structured["teach_week"] = teach_week
        # 官方教学周校准：覆盖本地推算的 current_week（前端课表默认按教学周显示）
        if teach_week and teach_week.get("isInSemester"):
            try:
                structured["current_week"] = int(teach_week["weekIndex"])
            except (TypeError, ValueError):
                pass
        return {
            **structured,
            "source": self._tool_source(result),
            "limitations": [result["message"]] if result.get("message") else [],
        }

    def _official_teach_week(self, principal: Principal) -> dict[str, Any] | None:
        """教务官方当前教学周（登录用户）；失败/未登录返回 None，不阻塞课表。"""
        if principal.auth_mode != "cas":
            return None
        sid = principal.principal_id
        now = time.monotonic()
        cached = _TEACH_WEEK_CACHE.get(sid)
        if cached and now - cached[0] < _TEACH_WEEK_TTL:
            return cached[1]
        try:
            from services.service_container import ServiceContainer

            with self._student_context(sid):
                container = ServiceContainer()
                if not container.has_cas():
                    return None
                data = container.cas_client.get_current_teach_week()
            if isinstance(data, dict) and data.get("weekIndex") is not None and "error" not in data:
                data["source"] = "jw"
                _TEACH_WEEK_CACHE[sid] = (now, data)
                return data
        except Exception:
            return None
        return None

    @staticmethod
    def _structured_schedule(
        courses: list[dict[str, Any]],
        *,
        semester: str = "",
    ) -> dict[str, Any]:
        from config import SEMESTER
        from utils.course_periods import parse_periods, periods_to_range
        from utils.schedule_parse import (
            normalize_time_str,
            parse_course_time,
            slot_clock_range,
            teaching_week,
        )

        semester_code = str(SEMESTER.get("name") or semester or "")
        start_text = str(SEMESTER.get("start_date") or "")
        total_weeks = int(SEMESTER.get("total_weeks") or 0)
        try:
            semester_start = Date.fromisoformat(start_text)
        except ValueError:
            semester_start = None
        current_week = (
            teaching_week(Date.today(), semester_start, total_weeks)
            if semester_start is not None else None
        )
        day_names = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
        structured_courses: list[dict[str, Any]] = []
        unparsed_courses: list[dict[str, Any]] = []

        for course_index, course in enumerate(courses):
            base = {
                "course_code": str(course.get("course_code") or ""),
                "course_name": str(course.get("course_name") or "未命名课程"),
                "teacher": str(course.get("teacher") or ""),
                "credits": float(course.get("credits") or 0),
                "semester": str(course.get("semester") or semester or semester_code),
                "time": str(course.get("time") or ""),
                "location": str(course.get("location") or ""),
            }
            raw_meetings: list[dict[str, Any]] = []
            if isinstance(course.get("meetings"), list):
                raw_meetings = [dict(item) for item in course["meetings"] if isinstance(item, dict)]
            if not raw_meetings and course.get("schedule_json"):
                try:
                    decoded = json.loads(str(course["schedule_json"]))
                    if isinstance(decoded, list):
                        raw_meetings = [dict(item) for item in decoded if isinstance(item, dict)]
                except (TypeError, ValueError):
                    raw_meetings = []
            if not raw_meetings and base["time"]:
                slots = parse_course_time(normalize_time_str(base["time"].replace("\n", ";")))
                for slot in slots:
                    clock = slot_clock_range(slot)
                    raw_meetings.append({
                        "day": slot.get("day"),
                        "weekday": slot.get("day_num"),
                        "weeks": slot.get("weeks_raw") or "",
                        "week_numbers": slot.get("week_numbers"),
                        "periods": slot.get("periods") or [],
                        "start_time": f"{clock[0] // 60:02d}:{clock[0] % 60:02d}" if clock else "",
                        "end_time": f"{clock[1] // 60:02d}:{clock[1] % 60:02d}" if clock else "",
                        "location": base["location"],
                        "raw": slot.get("raw") or base["time"],
                    })

            meetings: list[dict[str, Any]] = []
            incomplete_raw: list[str] = []
            for meeting_index, raw in enumerate(raw_meetings):
                try:
                    weekday = int(raw.get("weekday") or raw.get("day_num") or 0)
                except (TypeError, ValueError):
                    weekday = 0
                periods_value = raw.get("periods") or []
                periods = (
                    parse_periods(",".join(str(value) for value in periods_value))
                    if isinstance(periods_value, list)
                    else parse_periods(str(periods_value))
                )
                period_range = periods_to_range(periods)
                start_time = str(raw.get("start_time") or (period_range or {}).get("start") or "")
                end_time = str(raw.get("end_time") or (period_range or {}).get("end") or "")
                raw_weeks = raw.get("week_numbers")
                week_numbers: list[int] = []
                if isinstance(raw_weeks, list):
                    for value in raw_weeks:
                        try:
                            week = int(value)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= week <= total_weeks:
                            week_numbers.append(week)
                if not week_numbers and raw.get("weeks"):
                    week_slots = parse_course_time(f"周一 {raw['weeks']}")
                    if week_slots:
                        week_numbers = list(week_slots[0].get("week_numbers") or [])
                week_numbers = sorted(set(week_numbers))
                if weekday not in day_names or not start_time or not end_time or not week_numbers:
                    incomplete_raw.append(str(raw.get("raw") or base["time"] or "排课信息不完整"))
                    continue
                period_label = str(raw.get("period_label") or (period_range or {}).get("periods_text") or "")
                meetings.append({
                    "meeting_id": f"{base['course_code'] or course_index}-{meeting_index}-{weekday}-{start_time}",
                    "weekday": weekday,
                    "day": day_names[weekday],
                    "week_numbers": week_numbers,
                    "weeks": str(raw.get("weeks") or ""),
                    "periods": periods,
                    "period_label": period_label,
                    "start_time": start_time,
                    "end_time": end_time,
                    "location": str(raw.get("location") or base["location"] or ""),
                    "raw": str(raw.get("raw") or base["time"] or ""),
                })
            if meetings:
                structured_courses.append({**base, "meetings": meetings})
            if not meetings or incomplete_raw:
                unparsed_courses.append({
                    **base,
                    "reason": "排课缺少可确认的星期、周次或起止时间。",
                    "raw_schedule": "；".join(dict.fromkeys(incomplete_raw)) or base["time"],
                })

        return {
            "semester": semester or semester_code,
            "semester_code": semester_code,
            "semester_start": start_text,
            "total_weeks": total_weeks,
            "current_week": current_week,
            "courses": structured_courses,
            "unparsed_courses": unparsed_courses,
        }

    def program(self, principal: Principal) -> dict[str, Any]:
        profile = self._identity(principal)
        if principal.auth_mode == "demo":
            return {
                "program": self._load_demo().get("program") or {},
                "progress": None,
                "source": self._demo_source(),
                "banner": "演示数据：合成个人培养方案",
                "limitations": ["该方案仅用于验证页面与身份隔离。"],
            }

        from services.service_container import ServiceContainer
        from tools.program_tools import get_my_program, get_program_progress

        personal_tree = None
        with self._student_context(principal.principal_id):
            container = ServiceContainer()
            if container.has_cas():
                try:
                    personal_tree = container.cas_client.get_my_program_tree()
                except Exception:
                    personal_tree = None
            arguments = {
                "major": str(profile["major"]),
                "grade": str(profile["grade"]),
                "personal_tree": personal_tree,
            }
            program = get_my_program.invoke(arguments)
            grade_rows = self.courses(principal).get("grades") or []
            progress = get_program_progress.invoke({
                **arguments,
                "taken_courses": [str(row.get("course_name") or "") for row in grade_rows],
                "taken_credits": [float(row.get("credits") or 0) for row in grade_rows],
            })
        source = str(program.get("source") or "unavailable")
        banner = None
        limitations: list[str] = []
        if source == "generic":
            banner = "专业通用参考，不是个人培养方案"
            limitations.append("个人培养方案暂不可用，当前按已验证专业和年级显示通用方案。")
        elif source == "unavailable":
            limitations.append("暂未找到与当前已验证专业和年级匹配的培养方案。")
        return {
            "program": program,
            "progress": progress,
            "source": {
                "kind": source,
                "label": "个人培养方案" if source == "personal" else (banner or "暂无方案"),
                "demo": False,
            },
            "banner": banner,
            "limitations": limitations,
        }

    def _cas_overview(self, principal: Principal, profile: dict[str, Any]) -> dict[str, Any]:
        from tools.course_tools import calc_gpa, query_grade, query_schedule

        with self._student_context(principal.principal_id):
            gpa = calc_gpa.invoke({"student_id": principal.principal_id})
            grades = query_grade.invoke({"student_id": principal.principal_id})
            schedule = query_schedule.invoke({"student_id": principal.principal_id})
        grade_rows = grades.get("grades") or []
        return {
            "identity": profile,
            "metrics": {
                "gpa": gpa.get("gpa"),
                "completed_credits": gpa.get("total_credits"),
                "current_credits": round(sum(float(row.get("credits") or 0) for row in schedule.get("courses") or []), 1),
                "grade_count": len(grade_rows),
            },
            "recent_grades": grade_rows[-5:][::-1],
            "grades": grade_rows[::-1],
            "source": self._combined_source(gpa, grades, schedule),
            "limitations": [
                value for value in (gpa.get("message"), grades.get("message"), schedule.get("message")) if value
            ],
        }

    @staticmethod
    def _student_context(student_id: str):
        from contextlib import contextmanager
        from services.session_ctx import reset_student, set_student

        @contextmanager
        def context():
            token = set_student(student_id)
            try:
                yield
            finally:
                reset_student(token)

        return context()

    @staticmethod
    def _demo_source() -> dict[str, Any]:
        return {"kind": "demo_fixture", "label": "合成演示数据", "demo": True, "stale": False}

    @staticmethod
    def _tool_source(result: dict[str, Any]) -> dict[str, Any]:
        kind = str(result.get("source") or "unavailable")
        labels = {"real": "教务实时数据", "fallback": "本地缓存", "locked": "暂无数据"}
        return {"kind": kind, "label": labels.get(kind, kind), "demo": False, "stale": kind == "fallback"}

    def _combined_source(self, *results: dict[str, Any]) -> dict[str, Any]:
        kinds = {str(result.get("source") or "unavailable") for result in results}
        if "real" in kinds:
            return {"kind": "mixed" if len(kinds) > 1 else "real", "label": "实时数据与本地缓存" if len(kinds) > 1 else "教务实时数据", "demo": False, "stale": "fallback" in kinds}
        return {"kind": "fallback", "label": "本地缓存", "demo": False, "stale": True}
