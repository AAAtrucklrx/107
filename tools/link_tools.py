# -*- coding: utf-8 -*-
"""render_link —— 校园官方入口跳转工具（P4-2，路径 B 的 Agent 化实现）。

链接清单 config/links.yaml 只收录已核实来源（知识库权威文档 + 代码实际对接域名）；
场景不匹配时如实返回未找到，禁止 LLM 在回答中生成清单之外的 URL。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from langchain_core.tools import tool

from utils.logger import get_logger

log = get_logger("xiaowo.tools.links")

LINKS_FILE = Path(__file__).resolve().parents[1] / "config" / "links.yaml"
_LINKS_CACHE: list[dict] | None = None


def load_links() -> list[dict]:
    """读取链接清单（进程级缓存）。"""
    global _LINKS_CACHE
    if _LINKS_CACHE is None:
        data = yaml.safe_load(LINKS_FILE.read_text(encoding="utf-8")) or {}
        _LINKS_CACHE = list(data.get("links") or [])
    return _LINKS_CACHE


@tool
def render_link(scene: str) -> dict:
    """按场景给出校园官方系统/平台的跳转入口（选课退课、评教、缴费、图书馆等）。

    Args:
        scene: 场景描述（如 "退课"、"交学费"、"评教"、"借书"）

    Returns:
        {"found": True, "name": ..., "url": ..., "description": ..., "category": ...,
         "source": "官方"} 或 {"found": False, "note": ...}
    """
    scene = (scene or "").strip()
    if not scene:
        return {"found": False, "note": "请描述你要办理的事项（如退课/缴费/评教）"}

    links = load_links()
    best, best_hits = None, 0
    for entry in links:
        hits = sum(1 for kw in entry.get("scene") or [] if kw and kw in scene)
        if hits > best_hits:
            best, best_hits = entry, hits

    if best is None:
        # 兜底：场景词直接命中名称或描述
        for entry in links:
            if scene in str(entry.get("name", "")) or scene in str(entry.get("description", "")):
                best = entry
                break

    if best is None:
        return {
            "found": False,
            "note": f"链接清单中没有匹配「{scene}」的官方入口。回答时如实告知不知道入口，"
                    f"禁止编造 URL，可建议用户查阅教务处官网 teach.ustc.edu.cn 或知识库检索。",
            "source": "官方",
        }

    return {
        "found": True,
        "name": best["name"],
        "url": best["url"],
        "description": best.get("description", ""),
        "category": best.get("category", ""),
        "matched_keywords": [kw for kw in (best.get("scene") or []) if kw in scene],
        "source": "官方",
    }
