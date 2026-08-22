# -*- coding: utf-8 -*-
"""query_activities —— 青春科大（第二课堂）活动实时查询工具（P4-B）。

实时拉取 young.ustc.edu.cn「报名中」活动（10 分钟进程缓存防打爆平台），
支持关键词/分类/时间窗过滤；token 失效时如实报错并给更新指引，不造假数据。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from langchain_core.tools import tool

from config import YOUNG_TOKEN
from utils.logger import get_logger

log = get_logger("xiaowo.tools.activities")

_CACHE_TTL = 600  # 秒
_cache: dict = {"ts": 0.0, "acts": None, "err": ""}


def _fetch_enrolment_cached():
    """带 TTL 缓存的「报名中」拉取；token 失效时回退本地快照（source 如实标注）。"""
    now = time.time()
    if _cache["acts"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["acts"], None
    try:
        from services.young_client import YoungService
        acts = YoungService.from_token(YOUNG_TOKEN).fetch_enrolment_activities()
        _cache.update(ts=now, acts=acts, err="")
        return acts, None
    except Exception as e:  # noqa: BLE001
        log.warning(f"青春科大活动实时拉取失败: {e}")
        # P4-D 回退：本地快照（young_snapshot.json 的 enrolment 段），YoungActivity 同构
        snap_acts, snap_at = _load_snapshot_activities()
        if snap_acts:
            _cache.update(ts=now, acts=snap_acts, err="")
            return snap_acts, (f"本地缓存（快照于 {snap_at}；实时拉取失败：{str(e)[:60]}；"
                               "管理员可登录 young.ustc.edu.cn 更新 YOUNG_TOKEN）")
        hint = ("青春科大登录态（token）已失效且无本地快照。"
                "请管理员登录 young.ustc.edu.cn 后从浏览器 localStorage 更新 .env 的 YOUNG_TOKEN"
                "（约 7 天有效期），或运行 scripts/crawl_young.py 生成快照。")
        _cache.update(ts=now, acts=None, err=hint)
        return None, hint


def _load_snapshot_activities():
    """读快照「报名中」段 → YoungActivity 列表（字段同构，含 stale 标记用 description 前缀）。"""
    import json
    from pathlib import Path

    from services.young_client import YoungActivity
    snap_file = Path(__file__).resolve().parents[1] / "scripts" / "data" / "young_personal" / "young_snapshot.json"
    try:
        snap = json.loads(snap_file.read_text(encoding="utf-8"))
        acts = [YoungActivity(
            id=a.get("id", ""), name=a.get("name", ""),
            start_time=a.get("start"), end_time=a.get("end"),
            apply_start=a.get("apply_start"), apply_end=a.get("apply_end"),
            organizer=a.get("organizer", ""), category=a.get("category", ""),
            module=a.get("module", ""), fav_count=a.get("fav_count", 0),
            people_num=a.get("people_num", 0), service_hour=a.get("service_hour", ""),
            description=a.get("description", ""),
        ) for a in snap.get("enrolment") or []]
        return acts, snap.get("fetched_at", "")
    except Exception as e:  # noqa: BLE001
        log.warning(f"活动快照读取失败: {e}")
        return [], ""


def _in_window(act, window: str, now: datetime) -> bool | None:
    """时间窗过滤：返回 None 表示无窗口信息不过滤。"""
    if not window:
        return None
    w = window.strip()
    deadline = act.apply_deadline
    start = act.start_dt
    if "截止" in w or "快" in w:  # 即将截止（3 天内）
        if deadline is None:
            return False
        return 0 <= (deadline - now).total_seconds() <= 3 * 86400
    if "周末" in w:
        if start is None:
            return False
        return start.isoweekday() in (6, 7)
    if "本周" in w or "这周" in w or "本周内" in w:
        ref = start or deadline
        if ref is None:
            return False
        monday = now.date() - timedelta(days=now.isoweekday() - 1)
        return monday <= ref.date() <= monday + timedelta(days=6)
    return None


@tool
def query_activities(keyword: str = "", category: str = "",
                     time_window: str = "", limit: int = 8,
                     student_id: str = None) -> dict:
    """查询青春科大（第二课堂）当前可报名的活动列表（实时数据）。

    Args:
        keyword: 关键词，匹配活动名/简介/主办方（如 "讲座"、"辩论"、"志愿服务"）
        category: 分类过滤（如 "单次项目"、"系列项目"）
        time_window: 时间窗（"即将截止"/"周末"/"本周"）
        limit: 返回条数上限（默认 8）
        student_id: 学号（登录用户自动注入）

    Returns:
        {"count": N, "activities": [...], "fetched_at": ...,
         "source": "实时数据（青春科大 young.ustc.edu.cn）"} 或 {"error": ...}
    """
    acts, err = _fetch_enrolment_cached()
    if acts is None:
        return {"error": err, "source": "实时数据（青春科大）"}

    source = ("实时数据（青春科大 young.ustc.edu.cn）" if not err
              else f"本地缓存（青春科大快照）——{err}")

    now = datetime.now()
    kw = (keyword or "").strip().lower()
    cat = (category or "").strip()
    out = []
    for a in acts:
        if kw:
            hay = f"{a.name} {a.description} {a.organizer}".lower()
            if kw not in hay:
                continue
        if cat and cat not in (a.category or ""):
            continue
        keep = _in_window(a, time_window, now)
        if keep is False:
            continue
        out.append(a)

    out = out[: max(1, min(int(limit or 8), 20))]

    # P4-C 埋点：对话追问（asked）计入偏好画像（最多记 3 条防爆）
    if student_id:
        try:
            from services.service_container import ServiceContainer
            from services.activity_profile import record_interaction
            db = ServiceContainer().db
            for a in out[:3]:
                record_interaction(db, student_id, a, "asked")
        except Exception as e:  # noqa: BLE001
            log.debug(f"asked 埋点跳过: {e}")

    return {
        "count": len(out),
        "total_enrolment": len(acts),
        "activities": [{
            "name": a.name,
            "organizer": a.organizer,
            "category": a.category,
            "start": a.start_time,
            "end": a.end_time,
            "apply_end": a.apply_end,
            "people_num": a.people_num,
            "service_hour": a.service_hour,
            "description": (a.description or "")[:120],
        } for a in out],
        "fetched_at": datetime.fromtimestamp(_cache["ts"]).strftime("%Y-%m-%d %H:%M"),
        "source": source,
    }
