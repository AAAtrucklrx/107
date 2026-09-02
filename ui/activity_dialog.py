# -*- coding: utf-8 -*-
"""
activity_dialog.py — 校团委活动推荐弹窗（登录后展示）

- st.dialog 模态弹窗展示 Top 推荐（每天最多一次，按 用户+日期 持久化记录）
- 推荐理由可解释；底部可展开查看全部"报名中"活动
"""
import json
from datetime import date
from pathlib import Path

import streamlit as st

from config import PROJECT_ROOT

DIALOG_LOG_PATH = PROJECT_ROOT / "data" / "young_dialog_log.json"


def _load_log() -> dict:
    try:
        if DIALOG_LOG_PATH.exists():
            return json.loads(DIALOG_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_log(log: dict) -> None:
    try:
        DIALOG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIALOG_LOG_PATH.write_text(
            json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def shown_today(student_id: str) -> bool:
    """今天是否已展示过推荐弹窗（会话内标记 + 文件持久化双保险）"""
    if st.session_state.get("_young_dialog_shown") == student_id:
        return True
    return _load_log().get(student_id) == date.today().isoformat()


def mark_shown(student_id: str) -> None:
    """记录今天已展示（在打开弹窗前调用，防止 rerun 重复弹出）"""
    st.session_state["_young_dialog_shown"] = student_id
    log = _load_log()
    log[student_id] = date.today().isoformat()
    _save_log(log)


# ── P5-2 晨报独立去重（独立 log 文件 + 独立 session 键，与活动弹窗互不影响） ──
MORNING_BRIEF_LOG_PATH = PROJECT_ROOT / "data" / "morning_brief_log.json"
_MB_SESSION_KEY = "_morning_brief_shown"


def _load_mb_log() -> dict:
    try:
        if MORNING_BRIEF_LOG_PATH.exists():
            return json.loads(MORNING_BRIEF_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_mb_log(log: dict) -> None:
    try:
        MORNING_BRIEF_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        MORNING_BRIEF_LOG_PATH.write_text(
            json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def morning_brief_shown_today(student_id: str) -> bool:
    """晨报今天是否已展示（会话内 + 文件双保险，独立于活动弹窗）。"""
    if st.session_state.get(_MB_SESSION_KEY) == student_id:
        return True
    return _load_mb_log().get(student_id) == date.today().isoformat()


def mark_morning_brief_shown(student_id: str) -> None:
    """记录晨报今天已展示（打开弹窗前调用，防 rerun 重复弹）。"""
    st.session_state[_MB_SESSION_KEY] = student_id
    log = _load_mb_log()
    log[student_id] = date.today().isoformat()
    _save_mb_log(log)


def _fmt_activity_time(act) -> str:
    """活动时间展示：有具体时段显示时段，否则显示报名截止"""
    if act.start_time and act.end_time:
        return f"{act.start_time[:16].replace(' ', ' ')} ~ {act.end_time[:16]}"
    if act.apply_end:
        return f"报名截止 {act.apply_end[:16]}"
    return "时间待定"


@st.dialog("🎯 今日校团委活动推荐", width="large")
def show_activity_dialog(recs: list, all_activities: list):
    """模态弹窗：Top 推荐 + 全部活动列表（st.dialog 装饰器，调用即打开）"""
    if not recs:
        st.info("暂无可推荐的校团委活动")
        return

    st.caption("结合你的课表空闲时间与报名热度精选")
    for i, rec in enumerate(recs, 1):
        act = rec["activity"]
        with st.container(border=True):
            cols = st.columns([1, 3])
            with cols[0]:
                if act.pic_url:
                    st.image(act.pic_url, width="stretch")
                else:
                    st.markdown("#### 🎭")
            with cols[1]:
                st.markdown(f"**{i}. {act.name}**")
                st.caption(f"🏛 {act.organizer or act.sponsor or '校团委'}")
                st.caption(f"📅 {_fmt_activity_time(act)}")
                if getattr(act, "place_info", ""):
                    st.caption(f"📍 {act.place_info}")
                if getattr(act, "contact", ""):
                    st.caption(f"📞 {act.contact}")
                if act.service_hour:
                    st.caption(f"⏱ 志愿时长 {act.service_hour} 小时")
                st.markdown(f"💡 *{rec['reason']}*")

    with st.expander(f"查看全部 {len(all_activities)} 个报名中活动"):
        for act in all_activities:
            org = act.organizer or act.sponsor or "校团委"
            dl = act.apply_end or "未知"
            place = f"· 📍{act.place_info}" if getattr(act, "place_info", "") else ""
            st.markdown(f"- **{act.name}**（{org}）· 报名截止 {dl[:16]}{place}")
        st.caption("活动数据来自青春科大智慧团学平台（young.ustc.edu.cn）")


# ── P5-2 晨报独立弹窗（每日最多一次，去重见 morning_brief_log；与活动弹窗互不影响） ──
@st.dialog("🌅 今日晨报", width="large")
def show_morning_brief_dialog(brief: dict):
    """模态弹窗：聚合今日日程/今日考试/近期待办三板块（空板块已由 build_morning_brief 省略）。"""
    sections = brief.get("sections") or []
    if not sections:
        st.info("今天暂无晨报内容（无日程、无考试、无近期待办）")
        return
    st.caption(f"{brief.get('date', '')} 今日简报 · 数据缺失板块已自动隐藏")
    for sec in sections:
        items = sec.get("items") or []
        if not items:
            continue
        with st.container(border=True):
            st.markdown(f"**{sec.get('title', '')}**")
            for it in items:
                extra = []
                if it.get("time"):
                    extra.append(it["time"])
                if it.get("location"):
                    extra.append(it["location"])
                suffix = " · ".join(extra)
                st.markdown(f"- {it.get('title', '')} {('（' + suffix + '）') if suffix else ''}")
            note = sec.get("note") or sec.get("source")
            if note:
                st.caption(f"来源：{note}")
