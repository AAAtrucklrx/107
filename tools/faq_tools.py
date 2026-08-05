"""
小蜗 — 智能问答 Agent 工具
提供 FAQ 知识库检索和分类查询
"""

from langchain_core.tools import tool

from services.service_container import ServiceContainer


def _get_store():
    """获取 FAQ 向量存储实例"""
    return ServiceContainer().faq_store


@tool
def search_faq(query: str) -> dict:
    """
    在校园FAQ知识库中搜索与query相关的内容。
    用于回答科大学生关于教务流程、校园生活、办事指南等方面的问题。

    Args:
        query: 用户的自然语言问题，如 "学生证怎么补办"

    Returns:
        {
            "found": True/False,
            "results": [{"content": "...", "score": 0.85, "source": "...",
                         "category": "...", "is_official": True}, ...],
            "top_score": 0.85
        }
    """
    if not query.strip():
        return {"found": False, "results": [], "top_score": 0.0, "error": "请提供有效的问题"}

    try:
        store = _get_store()
    except RuntimeError:
        return {"found": False, "results": [], "top_score": 0.0, "error": "知识库未初始化"}

    return store.search(query)


@tool
def get_faq_categories() -> list[str]:
    """获取知识库中所有的FAQ分类列表"""
    try:
        return _get_store().get_categories()
    except RuntimeError:
        return []
