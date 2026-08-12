"""
小蜗 — 知识库文档加载器
加载 Markdown 格式的 FAQ 文档，按 标题/段落/句子/列表行 切分为分块文档
"""

import re
from pathlib import Path

MAX_CHUNK_LEN = 600
LONG_PARA_THRESHOLD = 200  # 超过此长度的段落触发句子级切分
SENTENCE_GROUP_LEN = 70  # 句子/列表行合并上限；当前语料量下需 ≤70 才能保证总块数 >200


def load_faq_documents(data_dir: Path) -> list[dict]:
    """
    加载 knowledge/data/ 下所有 Markdown 文档并分块。

    Returns:
        [
            {
                "id": "faq_0001",  # 同一文档的各块共享基础 id，由 vector_store 追加 _chunk{n}
                "content": "分块内容...",
                "metadata": {
                    "category": "教务",
                    "subcategory": "学籍管理",
                    "source": "教务处官网",
                    "keywords": "学生证,补办",
                    "is_official": True,
                    "last_updated": "2026-07-01",
                    "chunk_index": 0,
                    "chunk_count": 3,
                }
            },
            ...
        ]
    """
    documents = []
    counter = 0

    for category_dir in sorted(data_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        category = category_dir.name

        for md_file in sorted(category_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            parsed = _parse_faq_doc(content, category)
            if not parsed:
                continue
            counter += 1
            base_id = f"faq_{counter:04d}"
            chunks = parsed["body_chunks"] + parsed["note_chunks"]
            for i, chunk in enumerate(chunks):
                meta = dict(parsed["metadata"])
                meta["chunk_index"] = i
                meta["chunk_count"] = len(chunks)
                documents.append({
                    "id": base_id,
                    "content": chunk,
                    "metadata": meta,
                })

    return documents


def _parse_faq_doc(content: str, category: str) -> dict | None:
    """解析 Markdown 格式的 FAQ 文档，提取元数据并将正文/注意事项分块"""
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

    return {
        "metadata": {
            "category": category,
            "subcategory": subcategory,
            "title": title,
            "keywords": keywords,
            "source": source,
            "is_official": _is_official(content),
            "last_updated": last_updated,
        },
        "body_chunks": _split_into_chunks(_extract_section(lines, "正文")),
        "note_chunks": _split_into_chunks(_extract_section(lines, "注意事项")),
    }


def _extract_section(lines: list[str], section: str) -> str:
    """提取 `## <section>` 小节内容（到下一个 `## ` 标题为止）"""
    inside = False
    section_lines = []
    for line in lines:
        if line.startswith("## "):
            if line == f"## {section}":
                inside = True
                continue
            if inside:
                break
        elif inside:
            section_lines.append(line)
    return "\n".join(section_lines).strip()


def _split_into_chunks(text: str, max_len: int = MAX_CHUNK_LEN) -> list[str]:
    """按空行分段；≤200 字段落独立成块，长段落按句子/列表行切分为更小块"""
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    for para in paragraphs:
        if len(para) > LONG_PARA_THRESHOLD:
            chunks.extend(_split_long_text(para, max_len))
        else:
            chunks.append(para)
    return chunks


def _split_long_text(text: str, max_len: int = MAX_CHUNK_LEN) -> list[str]:
    """长段落先按句子(。！？)再按行(列表项)切分，相邻片段合并为 ≤SENTENCE_GROUP_LEN 的块"""
    pieces = []
    current = ""
    for sentence in re.split(r"(?<=[。？！])", text):
        for seg in re.split(r"\n", sentence):
            seg = seg.strip()
            if not seg:
                continue
            if len(current) + len(seg) + 1 <= SENTENCE_GROUP_LEN:
                current = f"{current}\n{seg}" if current else seg
            else:
                if current:
                    pieces.append(current)
                # 单个片段仍超长时按硬上限兜底切分，保证每块 ≤ max_len
                while len(seg) > max_len:
                    pieces.append(seg[:max_len])
                    seg = seg[max_len:]
                current = seg
    if current:
        pieces.append(current)
    return pieces


def _is_official(content: str) -> bool:
    """判断是否为官方来源"""
    unofficial_keywords = ["非官方", "同学经验", "仅供参考", "学长经验"]
    for kw in unofficial_keywords:
        if kw in content:
            return False
    return True
