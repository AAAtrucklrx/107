"""Query rewriting unit tests for the web evidence pipeline."""

from __future__ import annotations

import asyncio

from xiaowo_web.evidence.rewrite import (QueryRewriter, _RewritePayload, official_site_query, temporal_anchor, wechat_query)


def _run(coro):
    return asyncio.run(coro)


def test_short_question_skips_rewrite() -> None:
    rewriter = QueryRewriter(lambda _prompt: _RewritePayload(queries=["改写词"]))
    assert rewriter.wants_rewrite("学生证丢了") is False
    assert _run(rewriter.rewrite("学生证丢了")) is None


def test_long_question_returns_keyword_queries() -> None:
    def invoke(_prompt: str) -> dict:
        return {"queries": ["科大 教务处 选课通知", "2026 秋季本科生选课安排"]}

    rewriter = QueryRewriter(invoke)
    result = _run(rewriter.rewrite("中国科学技术大学2026年秋季学期本科生选课通知的最新安排是什么？"))
    assert result == ["科大 教务处 选课通知 2026", "2026 秋季本科生选课安排"]


def test_duplicate_or_original_queries_are_dropped() -> None:
    long_question = "这条问题很长用来触发改写流程并且验证去重与剔除原句的逻辑是否正确执行。"
    def invoke(_prompt: str) -> dict:
        return {"queries": [long_question, "科大选课通知", "科大选课通知"]}

    rewriter = QueryRewriter(invoke)
    result = _run(rewriter.rewrite(long_question))
    assert result == ["科大选课通知"]


def test_invalid_payload_falls_back_to_none() -> None:
    rewriter = QueryRewriter(lambda _prompt: {"bad": "shape"})
    assert _run(rewriter.rewrite("这条问题非常长，用来触发改写失败时的兜底路径而不至于让管线崩溃。")) is None
    assert rewriter.last_error is not None


def test_prompt_includes_short_hint_when_requested() -> None:
    seen: list[str] = []

    def invoke(prompt: str) -> dict:
        seen.append(prompt)
        return {"queries": ["更短关键词"]}

    rewriter = QueryRewriter(invoke)
    result = _run(rewriter.rewrite("这条问题同样非常长，用来检验简短改写提示词是否真正被拼接进了提示里。", short_hint=True))
    assert result == ["更短关键词"]
    assert "更简短" in seen[0]


def test_temporal_anchor_resolves_relative_years() -> None:
    assert temporal_anchor("今年选课时间") == str(2026)
    assert temporal_anchor("明年开学安排") == str(2027)
    assert temporal_anchor("2026年春季学期") == "2026"
    assert temporal_anchor("一般问题描述") is None


def test_year_is_appended_to_rewritten_queries_when_missing() -> None:
    year = str(2026)

    def invoke(_prompt: str) -> dict:
        return {"queries": ["科大选课通知"]}

    rewriter = QueryRewriter(invoke)
    result = _run(rewriter.rewrite("请问中国科学技术大学今年秋季学期本科生选课通知的最新安排是什么？"))
    assert result == [f"科大选课通知 {year}"]


def test_official_site_query_mapping() -> None:
    assert official_site_query("请问选课成绩在哪查") == "site:ustc.edu.cn 选课 成绩"
    assert official_site_query("研究生复试时间") == "site:ustc.edu.cn 研究生 复试"
    assert official_site_query("图书馆开放时间") == "site:ustc.edu.cn 开放时间 图书馆"
    assert official_site_query("今天天气怎么样") is None


def test_official_site_query_expanded_semester_words() -> None:
    """2026-09-03 词表扩充：开学/新生/自习/高考等高频事务词也要触发官方 site 查询；
    查询词保留问题业务词与年份锚（固定部门词会命中栏目列表页，业务词命中具体公告页）。"""
    assert official_site_query("2026年秋季学期什么时候开学") == "site:ustc.edu.cn 2026 开学"
    assert official_site_query("新生入学报到时间是哪天") == "site:ustc.edu.cn 新生 报到 入学"
    assert official_site_query("保研条件有哪些") == "site:ustc.edu.cn 保研"
    assert official_site_query("图书馆自习座位怎么预约") == "site:ustc.edu.cn 图书馆 自习 座位"
    assert official_site_query("高考录取分数线") == "site:ustc.edu.cn 分数线 高考 录取"
    assert official_site_query("四六级什么时候报名") == "site:ustc.edu.cn 四六级"
    assert official_site_query("今天的天气怎么样") is None


def test_wechat_query_uses_official_name_and_business_words() -> None:
    """微信通道查询改写：官方名称词+业务词；无科大触发词原样返回。"""
    assert wechat_query("中国科学技术大学2026年秋季学期何时开学？") == "中国科学技术大学 开学"
    assert wechat_query("中国科学技术大学图书馆开放时间是几点？") == "中国科学技术大学 开放时间 图书馆"
    assert wechat_query("蜗壳讲座") == "蜗壳讲座"  # 触发词不含"蜗壳"→不触发微信分支
    assert wechat_query("中科大讲座") == "中国科学技术大学 讲座"
    assert wechat_query("中科大校历") == "中国科学技术大学 校历"
    assert wechat_query("今天天气怎么样") == "今天天气怎么样"
