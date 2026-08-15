"""
小蜗 — 选课冲突检测与退补选压力评估工具（选课 H 项）

背景：轮4 实测中 LLM 只能凭时间字符串人工目测冲突，且会把周次不重叠的两门课
（如 力学B 周二11-12周 vs 热学B 周二13-18周）误判为"冲突"。本模块提供：

1. check_course_conflict      —— 已选课程之间的节次级冲突检测（精确到周次/节次/时钟）
2. evaluate_selection_pressure —— 退补选压力评估：学分总量 vs 上限、冲突、每日负荷、
                                   模拟退课/加课（无排课数据的课程如实标注，不臆造）

数据源：student_courses 本地缓存（fallback 语义，与 course_tools 一致带来源标识）；
未登录返回 locked 提示。评课库/缓存无推荐课程排课信息，候选课无数据时如实说明。
"""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from utils.schedule_parse import parse_course_time, slots_overlap

log = get_logger("xiaowo.tools.selection")

# 学分上限参考值（默认 30，最终以教务系统为准，工具输出中注明）
DEFAULT_CREDIT_CAP = 30.0


def _db():
    """获取数据库实例"""
    from services.service_container import ServiceContainer
    return ServiceContainer().db


def _norm_name(name: str) -> str:
    """课程名归一化（共享实现 utils/course_name）"""
    from utils.course_name import norm_course_name
    return norm_course_name(name)


def _load_selections(student_id: str) -> tuple[list[dict] | None, str, str]:
    """读取当前选课/课表缓存。

    返回 (courses, source, message)；未登录时 courses=None, source="locked"。
    """
    from tools.course_tools import _is_locked
    if _is_locked(student_id):
        return None, "locked", "需登录教务系统后获取个人选课数据"
    rows = _db().query(
        "SELECT * FROM student_courses WHERE student_id = ?", (student_id,))
    courses = [{
        "course_name": r.get("course_name", ""),
        "teacher": r.get("teacher", ""),
        "credits": r.get("credits") or 0,
        "time": r.get("time", ""),
        "location": r.get("location", ""),
        "semester": r.get("semester", ""),
    } for r in rows]
    return courses, "fallback", "本地缓存选课/课表数据，仅供参考"


def _match_names(courses: list[dict], names: list[str]) -> tuple[list[dict], list[str]]:
    """按归一化课程名过滤（支持包含匹配）；返回 (matched, missing)"""
    if not names:
        return courses, []
    matched, missing = [], []
    for name in names:
        n = _norm_name(name)
        hits = [c for c in courses if n and n in _norm_name(c["course_name"])]
        if hits:
            matched.extend(hits)
        else:
            missing.append(name)
    # 去重（同名同课只保留一次）
    seen, unique = set(), []
    for c in matched:
        k = _norm_name(c["course_name"])
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique, missing


def _course_slots(course: dict) -> list[dict]:
    """课程时间 → 时间段列表（无时间/无法解析 → []）"""
    return parse_course_time(course.get("time") or "")


def _pairwise(courses: list[dict]) -> tuple[list[dict], list[str]]:
    """两两比较 → (冲突列表, 时间不全课程名列表)。

    冲突条目：{course_a, course_b, day, a_time, b_time, reason, weeks_unknown}
    """
    conflicts: list[dict] = []
    incomplete: list[str] = []
    for i, a in enumerate(courses):
        sa = _course_slots(a)
        if not sa:
            incomplete.append(a["course_name"])
            continue
        for b in courses[i + 1:]:
            sb = _course_slots(b)
            if not sb:
                continue
            for x in sa:
                for y in sb:
                    verdict, info = slots_overlap(x, y)
                    if verdict == "conflict":
                        conflicts.append({
                            "course_a": a["course_name"],
                            "course_b": b["course_name"],
                            "day": x.get("day") or y.get("day") or "?",
                            "a_time": a.get("time", ""),
                            "b_time": b.get("time", ""),
                            "reason": info.get("reason", "时间重叠"),
                            "weeks_unknown": bool(info.get("weeks_unknown")),
                        })
                        break
                else:
                    continue
                break
    # 冲突对去重（同对多时段只报一次）
    seen, unique = set(), []
    for c in conflicts:
        key = tuple(sorted([_norm_name(c["course_a"]), _norm_name(c["course_b"])]))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique, incomplete


def _daily_stats(courses: list[dict]) -> tuple[dict, str]:
    """每日负荷分布 → ({'周一': {course_count, slot_count, period_count}, ...}, busiest_day)"""
    daily: dict[str, dict] = {}
    for c in courses:
        for s in _course_slots(c):
            day = s.get("day") or "未知"
            d = daily.setdefault(day, {"course_count": 0, "slot_count": 0, "period_count": 0})
            d["slot_count"] += 1
            d["period_count"] += len(s.get("periods") or []) or 1
    for c in courses:
        slots = _course_slots(c)
        days = {s.get("day") for s in slots if s.get("day")}
        for day in days:
            daily.setdefault(day, {"course_count": 0, "slot_count": 0, "period_count": 0})
            daily[day]["course_count"] += 1
    busiest = max((d for d in daily if d != "未知"),
                  key=lambda k: (daily[k]["slot_count"], daily[k]["course_count"]),
                  default="")
    return daily, busiest


def _stats(courses: list[dict], cap: float) -> dict:
    """汇总统计：学分/冲突/每日分布/时间不全"""
    total = round(sum(float(c.get("credits") or 0) for c in courses), 1)
    conflicts, incomplete = _pairwise(courses)
    daily, busiest = _daily_stats(courses)
    return {
        "course_count": len(courses),
        "total_credits": total,
        "credit_cap": cap,
        "margin": round(cap - total, 1),
        "over_cap": total > cap,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "time_incomplete": incomplete,
        "daily": daily,
        "busiest_day": busiest,
    }


def _suggestions(stats: dict) -> list[str]:
    """基于统计自动生成提示（不臆造，仅陈述数据事实）"""
    out: list[str] = []
    if stats["over_cap"]:
        out.append(f"当前总学分 {stats['total_credits']} 已超过 {stats['credit_cap']} 学分参考上限"
                   f"（超出 {abs(stats['margin'])}），建议退掉部分课程或与教务确认上限")
    elif stats["course_count"] and stats["total_credits"] >= stats["credit_cap"] - 2:
        out.append(f"当前总学分 {stats['total_credits']} 接近 {stats['credit_cap']} 学分参考上限"
                   f"（余 {stats['margin']}），再加课需谨慎")
    if stats["conflict_count"]:
        pairs = "、".join(f"{c['course_a']}×{c['course_b']}" for c in stats["conflicts"][:5])
        out.append(f"检测到 {stats['conflict_count']} 处时间冲突：{pairs}"
                   + ("（含周次未知的保守判定）" if any(c["weeks_unknown"] for c in stats["conflicts"]) else ""))
    if stats["time_incomplete"]:
        out.append(f"{len(stats['time_incomplete'])} 门课程时间不全（{', '.join(stats['time_incomplete'][:5])}），"
                   f"冲突判定可能不完整")
    if stats["busiest_day"]:
        bd = stats["daily"][stats["busiest_day"]]
        out.append(f"最忙 {stats['busiest_day']}：{bd['course_count']} 门课、{bd['slot_count']} 个时间段")
    return out


# ── Tool 定义 ──────────────────────────────────────────

@tool
def check_course_conflict(student_id: str = None, course_names: list[str] = None) -> dict:
    """
    检测已选课程之间的节次级时间冲突（精确到周次/节次；周次不重叠不算冲突）。

    Args:
        student_id: 学号（登录用户）
        course_names: 可选，只检测这些课程（课程名列表，也支持单个字符串；
                      不在已选数据中的课程会列入 missing，不臆造其排课时间）

    Returns:
        {"student_id", "total", "courses", "conflicts", "conflict_count",
         "time_incomplete", "missing", "source", "message"}
    """
    sid = student_id or ""
    courses, source, message = _load_selections(sid)
    if courses is None:
        return {"student_id": sid, "total": 0, "courses": [], "conflicts": [],
                "conflict_count": 0, "time_incomplete": [], "missing": [],
                "source": source, "message": message}

    names = course_names or []
    if isinstance(names, str):
        names = [names]
    names = [str(n).strip() for n in names if str(n).strip()]
    checked, missing = _match_names(courses, names)
    if not checked:
        return {"student_id": sid, "total": 0, "courses": [], "conflicts": [],
                "conflict_count": 0, "time_incomplete": [], "missing": missing,
                "source": source,
                "message": f"未找到这些课程的选课/课表数据（{', '.join(missing) or '无'}）。"
                           f"评课库与本地缓存暂无推荐课程的排课时间，无法做节次级冲突检测"}

    conflicts, incomplete = _pairwise(checked)
    return {
        "student_id": sid,
        "total": len(checked),
        "courses": checked,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "time_incomplete": incomplete,
        "missing": missing,
        "source": source,
        "message": message if source == "fallback" else "",
        "note": "周次不重叠不算冲突；周次未知按重叠保守判定（已标注）。"
                "时间不全/无排课数据的课程无法参与精确检测",
    }


@tool
def evaluate_selection_pressure(student_id: str = None, add_courses: list[str] = None,
                                drop_courses: list[str] = None, credit_cap: float = None) -> dict:
    """
    评估当前选课的学分压力与时间负荷，支持模拟退课/加课。

    Args:
        student_id: 学号（登录用户）
        add_courses: 可选，模拟加课（课程名列表/单个字符串；无排课与学分数据的课程
                     列入 adds_pending 如实标注，不计入统计）
        drop_courses: 可选，模拟退课（课程名列表/单个字符串；未匹配到的列入 drops_missing）
        credit_cap: 可选，学分上限参考值（默认 30，最终以教务系统为准）

    Returns:
        {"student_id", "credit_cap", "current", "after_add_drop", "drops_applied",
         "drops_missing", "adds_pending", "suggestions", "source", "message"}
    """
    sid = student_id or ""
    cap = float(credit_cap) if credit_cap else DEFAULT_CREDIT_CAP
    courses, source, message = _load_selections(sid)
    if courses is None:
        return {"student_id": sid, "credit_cap": cap, "current": None,
                "after_add_drop": None, "drops_applied": [], "drops_missing": [],
                "adds_pending": [], "suggestions": [], "source": source, "message": message}

    def _to_list(v):
        if not v:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x).strip() for x in v if str(x).strip()]

    add_names = _to_list(add_courses)
    drop_names = _to_list(drop_courses)

    current = _stats(courses, cap)
    drops_applied, drops_missing, adds_pending = [], [], []
    after = None

    if drop_names or add_names:
        working = [dict(c) for c in courses]
        for name in drop_names:
            n = _norm_name(name)
            hits = [c for c in working if n and n in _norm_name(c["course_name"])]
            if hits:
                working = [c for c in working if c not in hits]
                drops_applied.append("、".join(h["course_name"] for h in hits))
            else:
                drops_missing.append(name)
        for name in add_names:
            n = _norm_name(name)
            already = [c for c in working if n and n in _norm_name(c["course_name"])]
            if already:
                continue  # 已在选课中，忽略
            adds_pending.append(name)  # 本地无该课排课/学分数据，如实标注
        after = _stats(working, cap)
        after["drops_applied"] = drops_applied
        after["adds_pending"] = adds_pending

    suggestions = _suggestions(current)
    if after and after["course_count"] != current["course_count"]:
        suggestions = _suggestions(after) or suggestions
        if drops_applied:
            suggestions.insert(0, f"已模拟退课 {len(drops_applied)} 门（{', '.join(drops_applied)}），"
                                  f"学分 {current['total_credits']} → {after['total_credits']}")

    return {
        "student_id": sid,
        "credit_cap": cap,
        "current": current,
        "after_add_drop": after,
        "drops_applied": drops_applied,
        "drops_missing": drops_missing,
        "adds_pending": adds_pending,
        "suggestions": suggestions,
        "source": source,
        "message": message if source == "fallback" else "",
        "note": "学分上限为参考值（默认 30），最终以教务系统为准；"
                "加课模拟仅对已有排课数据的课程有效，其余如实标注待查证",
    }
