# -*- coding: utf-8 -*-
"""活动偏好画像（P4-C）——个性化推荐的数据层。

三层信号（按优先级）：
1. 平台官方兴趣标签（young scMyLabel，冷启动先验，如"志愿服务/体育健身"）；
2. 行为流水（activity_interactions：shown/clicked/asked/favorited），
   90 天窗口按类别/主办方累积，log 衰减防爆刷；
3. 显式反馈（"多推荐讲座类"）——由对话层写入 extra_likes/extra_dislikes。

全部本地存储，不上传。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import get_logger

log = get_logger("xiaowo.activity_profile")

SNAPSHOT_FILE = Path(__file__).resolve().parents[1] / "scripts" / "data" / "young_personal" / "young_snapshot.json"
_INTERACTION_WINDOW_DAYS = 90
# 行为权重：收藏/点击 > 追问 > 曝光
_ACTION_WEIGHTS = {"favorited": 3.0, "clicked": 2.0, "asked": 1.5, "shown": 0.3}


def _db():
    from services.service_container import ServiceContainer
    return ServiceContainer().db


def ensure_tables(db=None) -> None:
    """幂等建表（老库兼容：schema.sql 已含，这里兜底）。"""
    db = db or _db()
    db.execute("""CREATE TABLE IF NOT EXISTS activity_preferences (
        student_id TEXT PRIMARY KEY,
        labels TEXT DEFAULT '[]',
        snapshot_at TEXT DEFAULT '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS activity_interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        activity_id TEXT DEFAULT '',
        activity_name TEXT DEFAULT '',
        action TEXT NOT NULL,
        category TEXT DEFAULT '',
        organizer TEXT DEFAULT '',
        ts TEXT DEFAULT CURRENT_TIMESTAMP)""")


def load_platform_labels(student_id: str) -> tuple[list[dict], str]:
    """从 young 快照读平台兴趣标签（无快照/token 失效时返回空）。"""
    try:
        if SNAPSHOT_FILE.exists():
            snap = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
            labels = snap.get("labels") or []
            if labels and student_id and str(snap.get("profile", {}).get("username", "")) == student_id:
                return labels, snap.get("fetched_at", "")
    except Exception as e:  # noqa: BLE001
        log.warning(f"读取 young 快照失败: {e}")
    return [], ""


def get_profile(db, student_id: str) -> dict:
    """取画像（不存在或标签为空时（重）冷启动：写平台标签先验）。"""
    ensure_tables(db)
    row = db.query_one(
        "SELECT labels, snapshot_at FROM activity_preferences WHERE student_id = ?", (student_id,))
    labels: list = []
    if row:
        try:
            labels = json.loads(row.get("labels") or "[]")
        except Exception:
            labels = []

    # 标签为空（首次冷启动失败/快照当时拉取被限流）→ 用最新快照重试一次
    if not labels:
        snap_labels, fetched_at = load_platform_labels(student_id)
        if snap_labels:
            db.execute(
                "INSERT OR REPLACE INTO activity_preferences (student_id, labels, snapshot_at) VALUES (?, ?, ?)",
                (student_id, json.dumps(snap_labels, ensure_ascii=False), fetched_at))
            log.info(f"活动画像（重）冷启动 {student_id}：平台标签 {len(snap_labels)} 个")
            return {"student_id": student_id, "labels": snap_labels, "snapshot_at": fetched_at}
        if not row:
            db.execute(
                "INSERT OR REPLACE INTO activity_preferences (student_id, labels, snapshot_at) VALUES (?, ?, ?)",
                (student_id, "[]", ""))
        return {"student_id": student_id, "labels": [], "snapshot_at": row.get("snapshot_at", "") if row else ""}

    return {"student_id": student_id, "labels": labels, "snapshot_at": row.get("snapshot_at", "")}


def record_interaction(db, student_id: str, activity: dict | object, action: str) -> None:
    """记录行为并即时更新（activity 可为 dict 或 YoungActivity）。"""
    ensure_tables(db)
    geta = (lambda k, d="": (activity.get(k, d) if isinstance(activity, dict) else getattr(activity, k, d)) or d)
    db.execute(
        """INSERT INTO activity_interactions
           (student_id, activity_id, activity_name, action, category, organizer)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (student_id, str(geta("id")), str(geta("name") or geta("itemName", ""))[:80],
         action, str(geta("category") or geta("itemCategory_dictText", "")),
         str(geta("organizer") or geta("businessDeptName", ""))))


def _window_start() -> str:
    return (datetime.now() - timedelta(days=_INTERACTION_WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


def behavior_weights(db, student_id: str) -> dict[str, float]:
    """90 天窗口内的类别/主办方行为权重（log 压缩，0~1 归一化）。"""
    ensure_tables(db)
    rows = db.query(
        """SELECT action, category, organizer FROM activity_interactions
           WHERE student_id = ? AND ts >= ?""", (student_id, _window_start()))
    raw: dict[str, float] = {}
    for r in rows:
        w = _ACTION_WEIGHTS.get(r.get("action") or "", 0.5)
        for key in (r.get("category") or "", r.get("organizer") or ""):
            key = key.strip()
            if key:
                raw[key] = raw.get(key, 0.0) + w
    if not raw:
        return {}
    import math
    squeezed = {k: 1.0 + math.log1p(v) for k, v in raw.items()}
    mx = max(squeezed.values())
    return {k: round(v / mx, 4) for k, v in squeezed.items()}


def label_names(profile: dict) -> list[str]:
    return [str(l.get("name", "")) for l in (profile.get("labels") or []) if l.get("name")]


def personal_score(activity, profile: dict, weights: dict[str, float]) -> tuple[float, str]:
    """个性化因子 0~1 + 理由。

    组成：行为权重（类别/主办方命中取最大）；平台标签词命中活动名/简介。
    """
    name = (getattr(activity, "name", "") or "")
    desc = (getattr(activity, "description", "") or "")
    organizer = (getattr(activity, "organizer", "") or "")
    category = (getattr(activity, "category", "") or "")
    text = f"{name} {desc}"

    best, reason = 0.0, ""
    for key in (category, organizer):
        w = weights.get(key, 0.0)
        if w > best:
            best, reason = w, f"你近期关注{('「'+key+'」类') if key == category else ('主办方「'+key+'」')}活动"

    hit_labels = [n for n in label_names(profile) if n and (n in text or n[:2] in text)]
    if hit_labels:
        lab_score = min(1.0, 0.5 + 0.15 * len(hit_labels))
        if lab_score > best:
            best, reason = lab_score, f"匹配你的兴趣标签「{hit_labels[0]}」"
    return round(best, 4), reason
