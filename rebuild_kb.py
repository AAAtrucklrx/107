"""重建 FAQ 知识库向量索引（全量重建 ChromaDB）

用法：
    py rebuild_kb.py            # 预览：仅提示将执行的操作，不删除任何数据
    py rebuild_kb.py --yes      # 先备份当前索引，再执行全量重建
"""
import sys, os, shutil
from datetime import datetime

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CHROMA_PERSIST_DIR, KNOWLEDGE_DATA_DIR, PROJECT_ROOT
from knowledge.vector_store import FAQVectorStore, _nuke_chroma_db
from knowledge.document_loader import load_faq_documents


def _backup_persist_dir() -> str | None:
    """把现有 chroma_db 备份到 knowledge_backup_<时间戳>，返回备份路径；无索引时返回 None"""
    if not os.path.isdir(CHROMA_PERSIST_DIR):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = str(PROJECT_ROOT / f"knowledge_backup_{ts}")
    shutil.copytree(CHROMA_PERSIST_DIR, backup_dir)
    return backup_dir


def main() -> int:
    if "--yes" not in sys.argv:
        print("此操作会删除并重建 ChromaDB 向量索引，不可撤销。")
        print("如确认执行，请显式传入 --yes: py rebuild_kb.py --yes")
        return 1

    # 1. 备份旧索引（重建失败时可用备份恢复）
    backup_dir = _backup_persist_dir()
    if backup_dir:
        print(f"已备份旧索引: {backup_dir}")
    else:
        print("无现有索引，跳过备份")

    # 2. 清空旧索引并重建
    try:
        _nuke_chroma_db(CHROMA_PERSIST_DIR)
        print("Old index cleared")

        store = FAQVectorStore(persist_dir=CHROMA_PERSIST_DIR)
        docs = load_faq_documents(KNOWLEDGE_DATA_DIR)
        print(f"Loaded {len(docs)} docs")
        store.add_documents(docs)
        print(f"Built index: {store.count()} vectors, embed_method={store._embed_method}")
    except Exception as e:
        print(f"重建失败: {e}")
        if backup_dir:
            print(f"可恢复: 将 {backup_dir} 下的内容复制回 {CHROMA_PERSIST_DIR}")
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
