"""重建 FAQ 知识库向量索引（全量重建 ChromaDB）"""
import sys, os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CHROMA_PERSIST_DIR, KNOWLEDGE_DATA_DIR
from knowledge.vector_store import FAQVectorStore, _nuke_chroma_db
from knowledge.document_loader import load_faq_documents

# 清空旧索引
_nuke_chroma_db(CHROMA_PERSIST_DIR)
print("Old index cleared")

# 重建
store = FAQVectorStore(persist_dir=CHROMA_PERSIST_DIR)
docs = load_faq_documents(KNOWLEDGE_DATA_DIR)
print(f"Loaded {len(docs)} docs")
store.add_documents(docs)
print(f"Built index: {store.count()} vectors, embed_method={store._embed_method}")

# 检索测试
tests = ["学生证丢了怎么办", "图书馆几点关门", "校车时刻表", "大研计划什么时候申请", "GPA怎么计算", "食堂"]
for q in tests:
    r = store.search(q)
    status = "OK" if r["found"] else "MISS"
    print(f"  [{status}] '{q}' -> score={r['top_score']}")
