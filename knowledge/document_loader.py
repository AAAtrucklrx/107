"""
小蜗 — 知识库文档加载器
加载 Markdown 格式的 FAQ 文档，分割并向量化存储
"""

import os
import re
from pathlib import Path


def load_faq_documents(data_dir: Path) -> list[dict]:
    """
    加载 knowledge/data/ 下所有 Markdown 文档。

    Returns:
        [
            {
                "id": "faq_001",
                "content": "文档片段内容...",
                "metadata": {
                    "category": "教务",
                    "subcategory": "学籍管理",
                    "source": "教务处官网",
                    "keywords": "学生证,补办",
                    "is_official": True,
                    "last_updated": "2026-07-01"
                }
            },
            ...
        ]
    """
    documents = []
    counter = 0

    for category_dir in data_dir.iterdir():
        if not category_dir.is_dir():
            continue
        category = category_dir.name

        for md_file in category_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            # 解析文档 front matter
            parsed = _parse_faq_doc(content, category)
            if parsed:
                counter += 1
                parsed["id"] = f"faq_{counter:04d}"
                documents.append(parsed)

    return documents


def _parse_faq_doc(content: str, category: str) -> dict | None:
    """解析 Markdown 格式的 FAQ 文档，提取元数据和正文"""
    if not content.strip():
        return None

    lines = content.strip().split("\n")

    # 提取标题（第一行 # 开头）
    title = ""
    if lines[0].startswith("# "):
        title = lines[0][2:].strip()

    # 提取子分类（## 分类）
    subcategory = ""
    for line in lines:
        if "## 分类" in line:
            subcategory = line.split("## 分类")[-1].strip()
            break

    # 提取关键词
    keywords = ""
    for line in lines:
        if "## 关键词" in line:
            keywords = line.split("## 关键词")[-1].strip()
            break

    # 提取来源
    source = ""
    for i, line in enumerate(lines):
        if "## 相关链接" in line:
            if i + 1 < len(lines):
                source_line = lines[i + 1]
                source = source_line.strip("- ").strip()
            break

    # 提取最后更新时间
    last_updated = ""
    for line in lines:
        m = re.search(r"\| 最后更新\s*$", line)
        if m:
            last_updated = line.split("|")[-1].strip()
            break

    # 提取正文（## 正文 之后的内容）
    body = _extract_body(lines)

    # 判断是否官方来源
    is_official = _is_official(content)

    return {
        "content": body,
        "metadata": {
            "category": category,
            "subcategory": subcategory,
            "title": title,
            "keywords": keywords,
            "source": source,
            "is_official": is_official,
            "last_updated": last_updated,
        },
    }


def _extract_body(lines: list[str]) -> str:
    """提取正文部分"""
    in_body = False
    body_lines = []
    for line in lines:
        if "## 正文" in line:
            in_body = True
            continue
        if in_body:
            if line.startswith("## ") and "注意事项" not in line:
                break
            body_lines.append(line)
    return "\n".join(body_lines).strip()


def _is_official(content: str) -> bool:
    """判断是否为官方来源"""
    unofficial_keywords = ["非官方", "同学经验", "仅供参考", "学长经验"]
    for kw in unofficial_keywords:
        if kw in content:
            return False
    return True