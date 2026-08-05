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
                if act.service_hour:
                    st.caption(f"⏱ 志愿时长 {act.service_hour} 小时")
                st.markdown(f"💡 *{rec['reason']}*")

    with st.expander(f"查看全部 {len(all_activities)} 个报名中活动"):
        for act in all_activities:
            org = act.organizer or act.sponsor or "校团委"
            dl = act.apply_end or "未知"
            st.markdown(f"- **{act.name}**（{org}）· 报名截止 {dl[:16]}")
        st.caption("活动数据来自青春科大智慧团学平台（young.ustc.edu.cn）")
