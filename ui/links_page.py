# -*- coding: utf-8 -*-
"""校园导航页 —— 中科大已有工具与网站一览（P4-2 配套，纯跳转链接）。

数据源 config/links.yaml（与 render_link 共用）：只收录已核实官方来源；
新增站点只需在 yaml 加条目，本页自动分组呈现，零维护。
"""
from __future__ import annotations

import streamlit as st

from tools.link_tools import load_links


def render_links_page() -> None:
    st.title("🔗 校园导航")
    st.caption("科大常用的官方工具与网站一览（仅收录已核实官方来源；点击名称直达）。"
               "对话中直接问小蜗也可以，例如「学费在哪里交」。")

    links = load_links()
    if not links:
        st.warning("链接清单为空（config/links.yaml）")
        return

    # 按 yaml 声明顺序分组，保持分类稳定
    categories: dict[str, list[dict]] = {}
    for entry in links:
        categories.setdefault(entry.get("category", "其他"), []).append(entry)

    for cat, items in categories.items():
        st.header(cat)
        for e in items:
            st.markdown(
                f"- **[{e['name']}]({e['url']})** — {e.get('description', '')}")
