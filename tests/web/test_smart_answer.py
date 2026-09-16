"""百度智能搜索生成直达答复（web_answer_mode=smart）——提示词工程、来源标注与失败回退。"""

from __future__ import annotations

import asyncio

from tests.web.helpers import make_settings
from tests.web.test_evidence_pipeline import FakeCrawler, FixedExtractor
from xiaowo_web.evidence.models import CrawledPage, SearchBatch
from xiaowo_web.evidence.pipeline import EvidencePipeline
from xiaowo_web.evidence.url_security import UrlGuard


def _smart_settings(tmp_path, *, mode: str = "smart") -> object:
    return make_settings(
        tmp_path,
        extra={
            "XIAOWO_SEARCH_PROVIDER": "baidu",
            "XIAOWO_BAIDU_SEARCH_KEY": "bce-v3/test-key",
            "XIAOWO_WEB_ANSWER_MODE": mode,
        },
    )


class SmartSearch:
    """带 generate() 的搜索客户端替身：smart 成功/失败可控。"""

    def __init__(self, *, text: str | None = None, error: Exception | None = None,
                 references: list[dict] | None = None) -> None:
        self.text = text
        self.error = error
        self.references = list(references or [])
        self.calls: list[dict] = []

    async def generate(self, query: str, *, system_prompt: str = "",
                       model: str | None = None, limit: int = 8) -> tuple[str, list[dict]]:
        self.calls.append({"query": query, "system_prompt": system_prompt, "model": model, "limit": limit})
        if self.error is not None:
            raise self.error
        return (self.text or "答复"), list(self.references)

    async def search(self, query: str, *, limit: int = 10) -> SearchBatch:
        return SearchBatch([])

    async def close(self) -> None:
        return None


def _pipeline(tmp_path, search, *, mode: str = "smart") -> EvidencePipeline:
    return EvidencePipeline(
        _smart_settings(tmp_path, mode=mode),
        search,
        FakeCrawler({}),
        url_guard=UrlGuard(lambda _host, _port: ["8.8.8.8"]),
        extractor=FixedExtractor([]),
    )


def test_smart_answer_returns_generated_text(tmp_path) -> None:
    text = "2026年中秋节放假时间为9月25日至9月27日，共3天。"
    search = SmartSearch(text=text)
    bundle = asyncio.run(_pipeline(tmp_path, search).answer("2026年中秋节放假安排"))

    assert bundle.markdown == text
    assert bundle.terminal_reason == "AI_GENERATED"
    assert bundle.claims[0]["status"] == "generated"
    assert bundle.claims[0]["text"] == text
    # 来源徽标：AI 搜索生成（无引用），未附引用必须出现在限制里
    # references 为空 → 兜底来源仍是"未核实"，绝不伪装成官方
    assert any(s.get("source_id") == "s-ai-search" for s in bundle.sources)
    assert bundle.sources[0]["display_url"] is None
    assert any("未附独立引用" in item for item in bundle.limitations)
    # 提示词（含指令）现在作为单条 user 内容传入——该端点不支持 system 角色
    call = search.calls[0]
    assert call["query"].endswith("用户问题：\n2026年中秋节放假安排")
    assert "结论先行" in call["query"]
    assert call["model"] == "ernie-4.5-turbo-128k"


def test_smart_prompt_scopes_campus_questions_and_drops_abbreviation(tmp_path) -> None:
    """校内问句：检索词用全称 + 只采用本校官方来源 + 全文禁用简称。

    实测（2026-09-16）：「科大」会把百度带偏到科大讯飞／天津科技大学"AI科大"，
    本校来源 0/8、答案答成天津科技大学；改用全称并把关键词前置后升到 2/8。
    """
    search = SmartSearch(text="a")
    asyncio.run(_pipeline(tmp_path, search).answer("最新的转专业政策是什么？"))
    content = search.calls[0]["query"]
    assert content.startswith("检索关键词：中国科学技术大学 转专业")
    assert "只采用中国科学技术大学的官方信息" in content
    assert "未找到本校相关信息" in content
    assert "科大" not in content, "简称会被检索器匹配到别校"


def test_general_question_keeps_neutral_prompt(tmp_path) -> None:
    """非校内问句不得被套上本校约束（否则污染通用检索）。"""
    search = SmartSearch(text="答复")
    asyncio.run(_pipeline(tmp_path, search).answer("2026年中秋节放假安排"))
    content = search.calls[0]["query"]
    assert content.startswith("你是「小蜗」")
    assert "只采用中国科学技术大学的官方信息" not in content


def test_smart_answer_uses_real_references_as_sources(tmp_path) -> None:
    """B1/B2：references 变真实来源并按域名分层（原先被丢弃、用假来源顶替）。"""
    refs = [
        {"url": "https://www.teach.ustc.edu.cn/n1", "title": "教务处通知", "date": "2026-09-15"},
        {"url": "https://baijiahao.baidu.com/x", "title": "转专业自由吗", "website": "百家号"},
    ]
    search = SmartSearch(text="答复", references=refs)
    bundle = asyncio.run(_pipeline(tmp_path, search).answer("最新的转专业政策是什么？"))
    assert [s["level"] for s in bundle.sources[:2]] == ["official_primary", "unverified"]
    assert bundle.sources[0]["display_url"] == "https://www.teach.ustc.edu.cn/n1"
    assert bundle.sources[0]["domain"] == "www.teach.ustc.edu.cn"
    assert bundle.sources[1]["domain"] == "baijiahao.baidu.com"
    assert "命中本校官方来源 1 条" in " ".join(bundle.limitations)


def test_smart_answer_warns_when_no_official_source(tmp_path) -> None:
    """B4：校内问句一条官方来源都没有 → 必须显式示警。"""
    refs = [{"url": "https://baijiahao.baidu.com/x", "title": "转专业"}]
    search = SmartSearch(text="答复", references=refs)
    bundle = asyncio.run(_pipeline(tmp_path, search).answer("最新的转专业政策是什么？"))
    assert any("未命中本校官方来源" in item for item in bundle.limitations)


def test_campus_search_keywords_and_normalization() -> None:
    """检索词构造与简称规范化的单元断言。"""
    from xiaowo_web.evidence.rewrite import campus_search_keywords, normalize_school_terms

    assert campus_search_keywords("什么是量子力学") is None
    kw = campus_search_keywords("最新的转专业政策是什么？")
    assert kw and kw[0] == "中国科学技术大学" and "教务处" in kw
    assert "site:" not in " ".join(kw), "site: 对该端点无效，不得生成"
    assert normalize_school_terms("中科大放假") == "中国科学技术大学放假"
    assert normalize_school_terms("科大讯飞") == "科大讯飞", "公司名不得被替换"


def test_smart_failure_falls_back_to_classic(tmp_path) -> None:
    search = SmartSearch(error=RuntimeError("429"))
    bundle = asyncio.run(_pipeline(tmp_path, search).answer("2026年中秋节放假安排"))

    assert bundle.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert any("已回退通用检索" in item for item in bundle.limitations)


def test_classic_mode_skips_smart(tmp_path) -> None:
    search = SmartSearch(text="不应出现")
    bundle = asyncio.run(_pipeline(tmp_path, search, mode="classic").answer("2026年中秋节放假安排"))

    assert bundle.terminal_reason == "EVIDENCE_INSUFFICIENT"
    assert search.calls == []
