# -*- coding: utf-8 -*-
"""
activity_recommender.py — 校团委活动推荐引擎

方案：规则加权（报名紧迫度 + 课表空闲匹配 + 热度）+ MMR 多样性去重
- 冷启动友好：无需用户-活动交互历史（当前平台无公开行为数据）
- 可解释：每条推荐附带匹配理由（"报名还有2天截止"、"与空闲时间高度匹配"）
- 可扩展：Ranker 抽象预留机器学习模型接口（积累行为数据后无缝替换）

分层空闲匹配（产品决策）：
- 短期活动（st~et 跨度 <= 3 天）：活动时段与课表课程时段的空闲重叠比例
- 长期活动（跨度 > 3 天）：活动期间内"可参与日"（当天无课）占比
- 无时间信息：中性 0.5
"""
import math
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

from utils.course_periods import parse_periods, periods_to_range

try:
    from services.young_client import YoungActivity
except ImportError:  # 独立测试时使用
    YoungActivity = object

# 三因子权重（紧迫度 / 空闲匹配 / 热度）
WEIGHTS = {"urgency": 0.40, "freetime": 0.35, "hotness": 0.25}
# MMR 多样性强度（越大越看重相关度，越小越分散）
MMR_LAMBDA = 0.7

_DAY_PATTERN = re.compile(r":(\d)\(([^)]+)\)")


# ── 课表空闲匹配 ──────────────────────────────────

class FreeTimeMatcher:
    """基于每周课程时段的空闲匹配器（由 student_courses 构建）"""

    def __init__(self, courses_by_weekday: dict[int, list[tuple[str, str]]]):
        """courses_by_weekday: ISO 星期(1-7) → [(start "08:00", end "09:35"), ...]"""
        self.by_weekday = courses_by_weekday

    @classmethod
    def from_db(cls, db, student_id: str) -> "FreeTimeMatcher":
        """从 student_courses 表构建（time 字段如 "1~18周 5301 :3(3,4)"）"""
        rows = db.query("SELECT time FROM student_courses WHERE student_id = ?",
                        (student_id,))
        by_weekday: dict[int, list[tuple[str, str]]] = {}
        for r in rows:
            time_str = r.get("time", "") or ""
            for day_no, periods_str in _DAY_PATTERN.findall(time_str):
                periods = parse_periods(periods_str)
                tr = periods_to_range(periods) if periods else None
                if not tr:
                    continue
                iso_weekday = int(day_no) % 7 or 7  # 1=周一..7=周日
                by_weekday.setdefault(iso_weekday, []).append((tr["start"], tr["end"]))
        return cls(by_weekday)

    @staticmethod
    def _hhmm_to_minutes(t: str) -> int:
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    def occupied_on(self, dt: datetime) -> list[tuple[datetime, datetime]]:
        """某天的课程时段列表（datetime 区间）"""
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for start, end in self.by_weekday.get(dt.isoweekday(), []):
            s = self._hhmm_to_minutes(start)
            e = self._hhmm_to_minutes(end)
            out.append((day_start + timedelta(minutes=s),
                        day_start + timedelta(minutes=e)))
        return out

    def free_ratio(self, start: datetime, end: datetime) -> float:
        """活动时段与课程时段重叠比例 → 空闲占比 0~1"""
        if start >= end:
            return 0.5
        total = (end - start).total_seconds()
        if total <= 0:
            return 0.5
        busy = 0.0
        for os_, oe in self.occupied_on(start):
            ov = min(end, oe) - max(start, os_)
            if ov.total_seconds() > 0:
                busy += ov.total_seconds()
        return max(0.0, min(1.0, 1.0 - busy / total))

    def free_days(self, start: datetime, end: datetime) -> tuple[int, int]:
        """活动期间内可参与日计数：(空闲日数, 总日数)，当天无课程时段视为空闲日"""
        total = max(1, (end.date() - start.date()).days + 1)
        free = 0
        day = start.date()
        for _ in range(total):
            if not self.by_weekday.get(day.isoweekday()):
                free += 1
            day += timedelta(days=1)
        return free, total


# ── 因子评分 ──────────────────────────────────────

def score_urgency(act: "YoungActivity", now: datetime) -> float:
    """报名截止紧迫度 0~1：越临近截止越高"""
    deadline = act.apply_deadline
    if not deadline:
        return 0.3  # 无截止时间 → 中性偏低
    days_left = (deadline - now).total_seconds() / 86400.0
    if days_left <= 0:
        return 0.0
    if days_left <= 1:
        return 1.0
    if days_left <= 3:
        return 0.85
    if days_left <= 7:
        return 0.65
    if days_left <= 14:
        return 0.45
    return 0.25


def score_freetime(act: "YoungActivity", matcher: Optional[FreeTimeMatcher],
                   now: datetime) -> float:
    """分层空闲匹配 0~1"""
    if matcher is None:
        return 0.5
    start, end = act.start_dt, act.end_dt
    if not start or not end:
        return 0.5  # 无时间信息 → 中性
    span_days = (end - start).days
    if span_days <= 3:
        return matcher.free_ratio(start, end)
    free, total = matcher.free_days(start, end)
    return free / total if total else 0.5


def score_hotness(act: "YoungActivity") -> float:
    """热度原始分（log 压缩收藏数 + 参与人数）"""
    return 0.6 * math.log1p(act.fav_count) + 0.4 * math.log1p(act.people_num)


def _similarity(a: "YoungActivity", b: "YoungActivity") -> float:
    """多样性相似度：同类别 1.0 / 同模块 0.6 / 同组织 0.4 / 其他 0"""
    if a.category and a.category == b.category:
        return 1.0
    if a.module and a.module == b.module:
        return 0.6
    if a.organizer and a.organizer == b.organizer:
        return 0.4
    return 0.0


def _build_reason(act: "YoungActivity", now: datetime,
                  matcher: Optional[FreeTimeMatcher]) -> str:
    """生成可解释的推荐理由"""
    parts = []
    deadline = act.apply_deadline
    if deadline:
        days = (deadline - now).total_seconds() / 86400.0
        if days <= 0:
            parts.append("今天截止报名")
        elif days <= 1:
            parts.append("明天截止报名")
        elif days < 2:
            parts.append("报名即将截止")
        else:
            parts.append(f"报名还有{int(days)}天截止")
    if act.start_dt and act.end_dt and matcher:
        f = score_freetime(act, matcher, now)
        if f >= 0.8:
            parts.append("与你的空闲时间高度匹配")
        elif f >= 0.5:
            parts.append("空闲时间可参与")
    if act.fav_count > 0:
        parts.append(f"{act.fav_count}人收藏")
    return "、".join(parts) if parts else "近期校团委活动"


# ── 推荐主流程 ────────────────────────────────────

def recommend(activities: list["YoungActivity"],
              matcher: Optional[FreeTimeMatcher] = None,
              now: Optional[datetime] = None,
              top_n: int = 3,
              lambda_: float = MMR_LAMBDA) -> list[dict]:
    """
    综合推荐：规则加权 + MMR 多样性重排。

    Returns:
        [{"activity": YoungActivity, "score": float, "reason": str}, ...]（按推荐序）
    """
    now = now or datetime.now()

    # 1. 过滤：报名已截止 / 活动已结束
    candidates = []
    for act in activities:
        deadline = act.apply_deadline
        if deadline and deadline < now:
            continue
        if act.end_dt and act.end_dt < now:
            continue
        candidates.append(act)
    if not candidates:
        return []

    # 2. 三因子打分
    urgency = [score_urgency(a, now) for a in candidates]
    freetime = [score_freetime(a, matcher, now) for a in candidates]
    hot = [score_hotness(a) for a in candidates]

    # 3. 热度 min-max 归一化
    hmin, hmax = min(hot), max(hot)
    norm_hot = [0.5] * len(hot)
    if hmax > hmin:
        norm_hot = [(h - hmin) / (hmax - hmin) for h in hot]

    total = [WEIGHTS["urgency"] * u + WEIGHTS["freetime"] * f
             + WEIGHTS["hotness"] * h
             for u, f, h in zip(urgency, freetime, norm_hot)]

    # 4. MMR 贪心重排：λ*相关度 - (1-λ)*与已选最大相似度
    selected: list[int] = []
    remaining = list(range(len(candidates)))
    while len(selected) < top_n and remaining:
        best_idx, best_val = None, float("-inf")
        for i in remaining:
            if selected:
                sim = max(_similarity(candidates[i], candidates[j]) for j in selected)
            else:
                sim = 0.0
            val = lambda_ * total[i] - (1.0 - lambda_) * sim
            if val > best_val:
                best_val, best_idx = val, i
        selected.append(best_idx)
        remaining.remove(best_idx)

    # 5. 组装结果（附理由）
    result = []
    for i in selected:
        act = candidates[i]
        result.append({
            "activity": act,
            "score": round(total[i], 4),
            "reason": _build_reason(act, now, matcher),
        })
    return result


# ── 模型接口预留（未来可替换规则引擎） ─────────────

class BaseRanker:
    """推荐模型抽象：积累行为数据后实现此接口替换规则加权"""

    def rank(self, activities: list["YoungActivity"], context: dict) -> list[dict]:
        raise NotImplementedError


class RuleRanker(BaseRanker):
    """规则加权 + MMR（当前默认实现）"""

    def __init__(self, matcher: Optional[FreeTimeMatcher] = None,
                 now: Optional[datetime] = None):
        self.matcher = matcher
        self.now = now

    def rank(self, activities: list["YoungActivity"], context: dict = None) -> list[dict]:
        matcher = context.get("matcher") if context else None
        return recommend(activities, matcher or self.matcher,
                         self.now or datetime.now())
