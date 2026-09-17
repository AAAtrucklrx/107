# -*- coding: utf-8 -*-
"""query_activities —— 青春科大（第二课堂）活动实时查询工具（P4-B）。

实时拉取 young.ustc.edu.cn「报名中」活动（10 分钟进程缓存防打爆平台），
支持关键词/分类/时间窗过滤；token 失效时如实报错并给更新指引，不造假数据。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from langchain_core.tools import tool

from config import YOUNG_SNAPSHOT_PATH, YOUNG_TOKEN
from utils.logger import get_logger
from utils.semester_time import semester_now

log = get_logger("xiaowo.tools.activities")

_CACHE_TTL = 600  # 秒
_cache: dict = {"ts": 0.0, "acts": None, "err": ""}
# 详情补全缓存（item_id -> detail dict，进程级；避免重复打平台）
_detail_cache: dict = {}

# ── 活动描述的富文本清洗（2026-09-17）────────────────────────────────────────
# 背景：young 返回的 description 本身就是 HTML。实测 19 条报名中活动里 14 条含
# `<p>/<strong>/<br />` 或 `&ldquo;/&middot;/&mdash;` 这类实体，而前端只认 `<br>` + 5 个
# 实体 → 卡片和详情里把标签原样显示出来（用户看到的"乱码"）。在**工具出口统一清洗**，
# 这样 API、给 LLM 的工具摘要、推荐逻辑拿到的是同一份干净文本。
_BLOCK_TAG_RE = re.compile(r"<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/table|/section)\s*/?\s*>", re.I)
_ANY_TAG_RE = re.compile(r"<[^>]{0,200}>")
_NAMED_ENTITY = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ",
    "ldquo": "“", "rdquo": "”", "lsquo": "‘", "rsquo": "’", "middot": "·",
    "mdash": "—", "ndash": "–", "hellip": "…", "times": "×", "bull": "•",
    "copy": "©", "reg": "®", "trade": "™", "deg": "°", "laquo": "«", "raquo": "»",
    "ensp": " ", "emsp": " ", "thinsp": " ", "zwnj": "", "zwj": "",
}
_NAMED_RE = re.compile(r"&([a-zA-Z][a-zA-Z0-9]{1,9});")
_NUMERIC_RE = re.compile(r"&#(x?[0-9a-fA-F]{1,7});")


def _plain_text(value) -> str:
    """HTML 富文本 → 可读纯文本：块级标签转换行、剥掉其余标签、解实体、压空行。

    顺序讲究：**先剥标签再解实体** —— 这样 `&lt;script&gt;` 解出来是字面文本，
    不会被当成标签再剥一次（渲染端本来就转义，不存在注入面）。
    """
    text = str(value or "")
    if not text:
        return ""
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub("", text)

    def _named(match: "re.Match[str]") -> str:
        return _NAMED_ENTITY.get(match.group(1).lower(), match.group(0))

    def _numeric(match: "re.Match[str]") -> str:
        raw = match.group(1)
        try:
            code = int(raw[1:], 16) if raw[:1] in "xX" else int(raw)
            return chr(code) if 0 < code <= 0x10FFFF else match.group(0)
        except (ValueError, OverflowError):
            return match.group(0)

    # 解两遍：兼容 `&amp;ldquo;` 这种二次转义
    for _ in range(2):
        text = _NAMED_RE.sub(_named, text)
        text = _NUMERIC_RE.sub(_numeric, text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _enrich_places(acts, max_fill: int = 4, gap: float = 0.6):
    """列表缺地点/联系方式时用详情接口（/mobile/item/queryItemById）逐个兜底。

    实测 2026-09-02：仅 needPlaceApply=1 的活动在列表带 placeInfo，其余需详情查询；
    详情 142 字段包含 placeInfo/xq/linkMan/tel/formName。最多补 max_fill 条、
    每次间隔 gap 秒防打爆平台；任何异常只跳过该条（不阻断主流程）。
    """
    from services.young_client import YoungService
    svc = YoungService.from_token(YOUNG_TOKEN)
    filled = 0
    for a in acts:
        if filled >= max_fill:
            break
        item_id = getattr(a, "id", "")
        if not item_id or getattr(a, "place_info", ""):
            continue
        detail = _detail_cache.get(item_id)
        if detail is None:
            try:
                detail = svc.fetch_item_detail(item_id)
            except Exception as e:  # noqa: BLE001
                log.debug(f"活动详情补全失败 {item_id}: {e}")
                detail = None
            if detail:
                _detail_cache[item_id] = detail
            time.sleep(gap)
        if not isinstance(detail, dict):
            continue
        if not a.place_info:
            a.place_info = str(detail.get("placeInfo") or "")
        if not a.xq:
            a.xq = str(detail.get("xq") or "")
        if not a.contact:
            parts = [str(x).strip() for x in (detail.get("linkMan"), detail.get("tel")) if x]
            a.contact = " ".join(parts)
        if not a.form:
            a.form = str(detail.get("formName") or "")
        filled += 1
    return acts


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

    from services.young_client import YoungActivity
    snap_file = YOUNG_SNAPSHOT_PATH  # 单一来源 config.YOUNG_SNAPSHOT_PATH（crawl_young 写入、activity_profile 同读）
    try:
        snap = json.loads(snap_file.read_text(encoding="utf-8"))
        acts = [YoungActivity(
            id=a.get("id", ""), name=a.get("name", ""),
            start_time=a.get("start"), end_time=a.get("end"),
            apply_start=a.get("apply_start"), apply_end=a.get("apply_end"),
            organizer=a.get("organizer", ""), category=a.get("category", ""),
            module=a.get("module", ""), fav_count=a.get("fav_count", 0),
            people_num=a.get("people_num", 0), service_hour=a.get("service_hour", ""),
            description=_plain_text(a.get("description", "")),
            place_info=a.get("place_info", ""), xq=a.get("xq", ""),
            contact=a.get("contact", ""), form=a.get("form", ""),
        ) for a in snap.get("enrolment") or []]
        return acts, snap.get("fetched_at", "")
    except Exception as e:  # noqa: BLE001
        log.warning(f"活动快照读取失败: {e}")
        return [], ""


def _bj_today(now: datetime | None = None) -> datetime:
    """北京时区当前日期（活动时间为北京时间；容器 TZ 可能是 UTC，今日/本周判定必须锚定北京）。"""
    from zoneinfo import ZoneInfo
    try:
        if now is not None and now.tzinfo is not None:
            return now.astimezone(ZoneInfo("Asia/Shanghai"))
    except Exception:  # noqa: BLE001
        pass
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _in_window(act, window: str, now: datetime) -> bool | None:
    """时间窗过滤：返回 None 表示无窗口信息不过滤。"""
    if not window:
        return None
    w = window.strip()
    deadline = act.apply_deadline
    start = act.start_dt
    if "今日" in w or "今天" in w:  # 2026-09-04：今日窗口（活动开始日=北京今天）
        ref = start or deadline
        if ref is None:
            return False
        return ref.date() == _bj_today(now).date()
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
        today = _bj_today(now)
        monday = today.date() - timedelta(days=today.isoweekday() - 1)
        return monday <= ref.date() <= monday + timedelta(days=6)
    return None


# 活动返回条数安全上限。limit<=0 视为「全部」——实际条数受平台活动总量约束。
# 旧实现硬编码 min(limit, 20)：活动界面因此最多只能看到 20 条（2026-09-15 修）。
_ACTIVITY_LIMIT_CEILING = 500
_ACTIVITY_DEFAULT_LIMIT = 8


def _resolve_activity_limit(limit, total: int) -> int:
    """把 limit 解析为实际取用条数：**<=0 表示全部**，否则上限 _ACTIVITY_LIMIT_CEILING。

    返回 >=1 的整数，可直接用于切片与推荐器 top_n（切片对超出长度自动截断）。
    """
    if limit is None:
        limit = _ACTIVITY_DEFAULT_LIMIT  # 与函数签名默认值保持一致，避免显式 None 被当成「全部」
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = _ACTIVITY_DEFAULT_LIMIT
    if n <= 0:
        return max(1, int(total))
    return max(1, min(n, _ACTIVITY_LIMIT_CEILING))


@tool
def query_activities(keyword: str = "", category: str = "",
                     time_window: str = "", limit: int = _ACTIVITY_DEFAULT_LIMIT,
                     student_id: str = None) -> dict:
    """查询青春科大（第二课堂）当前可报名的活动列表（实时数据）。

    Args:
        keyword: 关键词，匹配活动名/简介/主办方（如 "讲座"、"辩论"、"志愿服务"）
        category: 分类过滤（如 "单次项目"、"系列项目"）
        time_window: 时间窗（"即将截止"/"周末"/"本周"）
        limit: 返回条数上限（默认 8；**传 0 或负数表示返回全部**）
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

    now = semester_now()
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

    # 2026-09-04 接线推荐引擎：紧迫度+课表空闲+热度+(登录)个性化 四因子 + MMR 多样性，
    # 全部推荐附带理由；推荐器失败/异常时回退原始顺序（降级不报错）
    reasons: dict[int, str] = {}
    try:
        from services.activity_recommender import FreeTimeMatcher, recommend

        matcher = None
        personal_profile = None
        if student_id:
            from services.service_container import ServiceContainer
            from services.activity_profile import get_profile
            db = ServiceContainer().db
            try:
                matcher = FreeTimeMatcher.from_db(db, student_id)
            except Exception:  # noqa: BLE001 — 课表解析失败 → 空闲因子中性
                matcher = None
            try:
                personal_profile = get_profile(db, student_id)
            except Exception:  # noqa: BLE001 — 画像不可用 → 退回三因子
                personal_profile = None

        ranked = recommend(
            out,
            matcher=matcher,
            now=now,
            top_n=_resolve_activity_limit(limit, len(out)),
            personal_profile=personal_profile,
        )
        if ranked:
            out = [item["activity"] for item in ranked]
            reasons = {id(item["activity"]): str(item.get("reason", "") or "") for item in ranked}
    except Exception as e:  # noqa: BLE001
        log.warning(f"活动推荐引擎异常，回退原始顺序: {e}")

    out = out[: _resolve_activity_limit(limit, len(out))]

    # 2026-09-02：展示集补全地点/联系人（仅实时模式；快照回退时 token 多已失效，详情接口同样不可用）
    if not err:
        out = _enrich_places(out)

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
            "apply_start": a.apply_start,
            "apply_end": a.apply_end,
            "place": a.place_info,
            "campus": a.xq,
            "contact": a.contact,
            "form": a.form,
            "people_num": a.people_num,
            "service_hour": a.service_hour,
            # 2026-09-17：清洗 HTML 且不再截断到 120 字（详情弹窗与卡片共用该字段；
            # 卡片的三行截断交给 CSS -webkit-line-clamp，自带省略号）
            "description": _plain_text(a.description),
            "reason": reasons.get(id(a), ""),
        } for a in out],
        "fetched_at": datetime.fromtimestamp(_cache["ts"]).strftime("%Y-%m-%d %H:%M"),
        "source": source,
    }
