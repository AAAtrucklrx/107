# -*- coding: utf-8 -*-
"""`llm_content` 的流式语义回归。

背景（2026-09-15 定位）：Web(SSE) 路径逐 chunk 调用 `llm_content`，而它内部 `.strip()`，
把**落在 chunk 边界的换行整段吃掉**——模型的 7 个换行经处理后剩 0 个，
Markdown 表格 / 列表 / 引用全部塌成一行。非流式（整篇 invoke）不受影响，
所以 Streamlit 版看着正常、Web 版格式崩坏，长期未被发现。
"""
from __future__ import annotations

from utils.llm_client import llm_content


class _Chunk:
    def __init__(self, content: str = "", reasoning: str | None = None) -> None:
        self.content = content
        self.additional_kwargs = {} if reasoning is None else {"reasoning_content": reasoning}


def test_strip_true_trims_whole_answer():
    assert llm_content(_Chunk("\n\n  你好  \n")) == "你好"


def test_strip_false_preserves_boundary_newlines():
    """回归：换行常落在 chunk 边界，strip 会把它们整段吃掉。"""
    chunks = ["小蜗来啦！", "\n", "| 课程 | 分数 |", "\n", "|---|---|", "\n", "| 数学 | 9.0 |"]
    joined = "".join(llm_content(_Chunk(c), strip=False) for c in chunks)
    assert joined.count("\n") == 3
    assert joined.splitlines()[2] == "|---|---|"


def test_strip_false_keeps_whitespace_only_chunk():
    """纯空白块（模型把换行/空格单独成块）必须原样保留，不能被丢弃。"""
    assert llm_content(_Chunk("\n"), strip=False) == "\n"
    assert llm_content(_Chunk(" "), strip=False) == " "


def test_strip_true_drops_whitespace_only_chunk():
    """对照：整篇语义下纯空白块本来就该被去掉。"""
    assert llm_content(_Chunk("\n")) == ""
    assert llm_content(_Chunk(" ")) == ""


def test_strip_false_still_skips_truly_empty_chunk():
    assert llm_content(_Chunk(""), strip=False) == ""


def test_reasoning_fallback_respects_strip_flag():
    assert llm_content(_Chunk("", reasoning="  思考内容  ")) == "思考内容"
    assert llm_content(_Chunk("", reasoning="  思考内容  "), strip=False) == "  思考内容  "


def test_none_response_is_empty():
    assert llm_content(None) == ""
