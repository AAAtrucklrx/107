"""重建 FAQ 知识库向量索引（全量重建 ChromaDB）

用法：
    py rebuild_kb.py            # 预览：仅提示将执行的操作，不删除任何数据
    py rebuild_kb.py --yes      # 执行全量重建
"""
import sys, os

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CHROMA_PERSIST_DIR, KNOWLEDGE_DATA_DIR
from knowledge.vector_store import FAQVectorStore, _nuke_chroma_db
from knowledge.document_loader import load_faq_documents


def _nested_chroma_dirs(persist_dir: str) -> set:
    """本目录下「自身就是一个 Chroma 实例」的子目录（如审核发布索引 web_approved）。"""
    out = set()
    for item in os.listdir(persist_dir):
        p = os.path.join(persist_dir, item)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "chroma.sqlite3")):
            out.add(item)
    return out


def main() -> int:
    if "--yes" not in sys.argv:
        print("此操作会删除并重建 ChromaDB 向量索引，不可撤销。")
        print("如确认执行，请显式传入 --yes: py rebuild_kb.py --yes")
        return 1

    try:
        before = _nested_chroma_dirs(CHROMA_PERSIST_DIR)
        if before:
            print(f"检测到嵌套的独立索引（不属本次重建范围）: {', '.join(sorted(before))}")

        # _nuke_chroma_db 会跳过「自带 chroma.sqlite3 的子目录」，因此发布索引不会被误删
        skipped = _nuke_chroma_db(CHROMA_PERSIST_DIR, keep=sorted(before))
        print("Old index cleared" + (f"（保留 {', '.join(sorted(skipped))}）" if skipped else ""))

        lost = before - set(skipped)
        if lost:
            print(f"⚠️ 本该保留的嵌套索引被删除: {sorted(lost)} —— 请立即从备份恢复，已中止重建")
            return 1

        store = FAQVectorStore(persist_dir=CHROMA_PERSIST_DIR)
        docs = load_faq_documents(KNOWLEDGE_DATA_DIR)
        print(f"Loaded {len(docs)} chunks")
        store.add_documents(docs)
        print(f"Built index: {store.count()} vectors, embed_method={store._embed_method}")

        # 分块统计与元数据完整性检查
        doc_ids = {d["id"] for d in docs}
        missing = {k: sum(1 for d in docs if k not in (d["metadata"] or {}))
                   for k in ("category", "subcategory", "source", "keywords", "title", "is_official", "last_updated", "chunk_index", "chunk_count")}
        overlong = sum(1 for d in docs if len(d["content"]) > 600)
        print(f"Chunk stats: 源文档数={len(doc_ids)}, 总块数={len(docs)}, 超600字块={overlong}")
        print(f"元数据缺失统计: {missing}")
    except Exception as e:
        print(f"重建失败: {e}")
        return 1

    # 3. 检索测试
    tests = ["学生证丢了怎么办", "图书馆几点关门", "校车时刻表", "大研计划什么时候申请", "GPA怎么计算", "食堂"]
    for q in tests:
        r = store.search(q)
        status = "OK" if r["found"] else "MISS"
        print(f"  [{status}] '{q}' -> score={r['top_score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
