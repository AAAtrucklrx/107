# -*- coding: utf-8 -*-
"""活动返回条数语义：`limit<=0` 表示**全部**。

背景（2026-09-15）：活动界面固定只能看到 12 条（路由默认 12，工具内还有 min(limit,20) 硬顶），
而平台上报名中活动实测有 80+ 条 —— 大部分被挡在界面外。
"""
from __future__ import annotations

import pytest

from tools.activity_tools import (
    _ACTIVITY_DEFAULT_LIMIT,
    _ACTIVITY_LIMIT_CEILING,
    _resolve_activity_limit,
)


@pytest.mark.parametrize("limit", [0, -1, -999])
def test_non_positive_limit_means_all(limit):
    assert _resolve_activity_limit(limit, 86) == 86
    assert _resolve_activity_limit(limit, 1) == 1


def test_positive_limit_passes_through():
    assert _resolve_activity_limit(5, 86) == 5
    assert _resolve_activity_limit(_ACTIVITY_DEFAULT_LIMIT, 86) == _ACTIVITY_DEFAULT_LIMIT


def test_limit_is_capped_by_ceiling_not_by_20():
    """回归：旧实现硬顶 20，导致活动界面看不到更多；现仅受宽松安全上限约束。"""
    assert _resolve_activity_limit(10_000, 86) == _ACTIVITY_LIMIT_CEILING
    assert _resolve_activity_limit(50, 86) == 50  # 旧实现会给 20


def test_none_or_invalid_falls_back_to_default_not_all():
    """显式 None / 非数字应退回默认 8，不能被误当成「全部」。"""
    assert _resolve_activity_limit(None, 86) == _ACTIVITY_DEFAULT_LIMIT
    assert _resolve_activity_limit("abc", 86) == _ACTIVITY_DEFAULT_LIMIT


def test_returns_at_least_one_for_empty_source():
    """数据源为空时也返回 >=1，保证可直接用于推荐器 top_n。"""
    assert _resolve_activity_limit(0, 0) >= 1
    assert _resolve_activity_limit(0, -5) >= 1


# ── 活动描述清洗（2026-09-17：乱码与截断修复）─────────────────────────────────

def test_plain_text_strips_html_tags_and_decodes_entities():
    """回归：young 的活动描述是 HTML —— 标签与实体绝不能外露到界面。"""
    from tools.activity_tools import _plain_text

    raw = ("<p><strong>演出时间：9月19日（周六）19:00</strong><br />"
           "活动书籍：伊恩&middot;瓦特《小说的兴起》</p>"
           "&ldquo;挑战杯&rdquo;&mdash;2026")
    got = _plain_text(raw)
    assert "<" not in got and ">" not in got, f"标签未剥净: {got!r}"
    assert "&middot;" not in got and "&ldquo;" not in got, f"实体未解码: {got!r}"
    assert "演出时间：9月19日（周六）19:00" in got
    assert "伊恩·瓦特" in got
    assert "“挑战杯”—2026" in got
    assert "\n" in got, "块级/<br> 标签应转成换行"


def test_plain_text_keeps_full_length_and_decodes_numeric_entities():
    """回归：不再截断到 120/200 字；数字实体（含十六进制）也要解码。"""
    from tools.activity_tools import _plain_text

    body = "补充说明。" * 40  # 200 字
    got = _plain_text(body + "&#39;&#x4e2d;&amp;ldquo;")
    assert len(got) > 150, f"正文被截断了: {len(got)}"
    assert got.endswith("'中“"), f"数字实体解码异常: {got[-6:]!r}"  # &#39; 是 ASCII 撇号


def test_plain_text_decodes_full_html5_entity_set():
    """回归：颜文字里的 &ograve; &forall; &oacute; 也要解码（手写实体表覆盖不到）。"""
    from tools.activity_tools import _plain_text

    got = _plain_text("欢迎加群交流（｡&ograve; &forall; &oacute;｡）&foo;")
    for raw in ("&ograve;", "&forall;", "&oacute;", "&foo;"):
        assert raw not in got, f"残留: {raw}"
    assert "ò" in got and "∀" in got and "ó" in got


def test_activity_output_mapping_uses_plain_text_without_cap():
    """守门：输出映射必须走 _plain_text，且不得再出现 [:120] 截断。"""
    import inspect

    from tools import activity_tools

    src = inspect.getsource(activity_tools)
    assert '"description": _plain_text(a.description),' in src
    assert '(a.description or "")[:120]' not in src

