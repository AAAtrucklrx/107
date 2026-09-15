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
