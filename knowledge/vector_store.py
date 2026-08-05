"""
小蜗 — 向量存储管理模块
基于 ChromaDB 的 FAQ 知识库检索
支持: OpenAI兼容API Embedding / 本地 SentenceTransformer / 关键词fallback
"""

import os
import sqlite3
import chromadb
from chromadb.config import Settings as ChromaSettings
from config import CHROMA_PERSIST_DIR, EMBEDDING_CONFIG, FAQ_TOP_K, FAQ_SIMILARITY_THRESHOLD, LLM_CONFIG
from utils.logger import get_logger

log = get_logger("xiaowo.vector")

# 不同 embedding 模式下的相似度阈值
THRESHOLD_MAP = {"api": 0.6, "local": 0.35, "fallback": 0.25}


class FAQVectorStore:
    """FAQ 知识库向量存储"""

    def __init__(self, persist_dir: str = CHROMA_PERSIST_DIR):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        self.collection_name = "faq_knowledge"
        self._embedding_model = None
        self._embed_method = None

        try:
            self.client = chromadb.PersistentClient(
                path=persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._init_collection()
        except Exception as e:
            # ChromaDB 持久化数据损坏时重建
            log.warning(f"ChromaDB init failed, rebuilding: {e}")
            _nuke_chroma_db(persist_dir)
            self.client = chromadb.PersistentClient(
                path=persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._init_collection()

    def _init_collection(self):
        existing = [c.name for c in self.client.list_collections()]
        if self.collection_name in existing:
            self.collection = self.client.get_collection(name=self.collection_name)
        else:
            self.collection = self.client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )

    @property
    def embedding_model(self):
        if self._embedding_model is None:
            self._embedding_model = self._init_embedding()
        return self._embedding_model

    def _init_embedding(self):
        # 策略1: OpenAI 兼容 API
        try:
            from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
            ef = OpenAIEmbeddingFunction(
                api_key=LLM_CONFIG["api_key"],
                api_base=LLM_CONFIG["base_url"],
                model_name="text-embedding-3-small",
            )
            ef(["test"])
            self._embed_method = "api"
            log.info("Embedding: API 模式")
            return ef
        except Exception as e1:
            log.warning(f"Embedding API 不可用: {e1}")

        # 策略2: 本地 SentenceTransformer
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(
                EMBEDDING_CONFIG["model_name"],
                device=EMBEDDING_CONFIG["device"],
            )
            self._embed_method = "local"
            log.info("Embedding: 本地模型模式")
            return model
        except Exception as e2:
            log.warning(f"Embedding 本地模型不可用: {e2}")

        # 策略3: 关键词 fallback
        log.info("Embedding: 关键词 fallback 模式")
        self._embed_method = "fallback"
        return _KeywordEmbedder()

    def add_documents(self, documents: list[dict]):
        if not documents:
            return
        ids = [d["id"] for d in documents]
        contents = [d["content"] for d in documents]
        metadatas = [d["metadata"] for d in documents]
        embeddings = self.embedding_model.encode(contents).tolist()
        self.collection.add(
            ids=ids, embeddings=embeddings,
            documents=contents, metadatas=metadatas,
        )

    def search(self, query: str, top_k: int = FAQ_TOP_K) -> dict:
        if not query.strip() or self.collection.count() == 0:
            return {"found": False, "results": [], "top_score": 0.0}

        query_embedding = self.embedding_model.encode([query]).tolist()
        raw = self.collection.query(
            query_embeddings=query_embedding,
            n_results=min(top_k, self.collection.count()),
        )

        results = []
        top_score = 0.0
        if raw["ids"] and raw["ids"][0]:
            for i in range(len(raw["ids"][0])):
                score = 1 - raw["distances"][0][i]
                top_score = max(top_score, score)
                meta = raw["metadatas"][0][i] if raw["metadatas"] else {}
                results.append({
                    "content": raw["documents"][0][i],
                    "score": round(score, 4),
                    "source": meta.get("source", "未知来源"),
                    "category": meta.get("category", "其他"),
                    "subcategory": meta.get("subcategory", ""),
                    "is_official": meta.get("is_official", True),
                    "title": meta.get("title", ""),
                })

        return {
            "found": top_score >= THRESHOLD_MAP.get(self._embed_method, FAQ_SIMILARITY_THRESHOLD),
            "results": results,
            "top_score": round(top_score, 4),
        }

    def get_categories(self) -> list[str]:
        if self.collection.count() == 0:
            return []
        metas = self.collection.get()["metadatas"]
        categories = set()
        for m in metas:
            if m and "category" in m:
                categories.add(m["category"])
        return sorted(categories)

    def count(self) -> int:
        return self.collection.count()

    def clear(self):
        self.client.delete_collection(name=self.collection_name)
        self._init_collection()


def _nuke_chroma_db(persist_dir: str):
    """彻底清除 ChromaDB 持久化数据"""
    import shutil
    for item in os.listdir(persist_dir):
        item_path = os.path.join(persist_dir, item)
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
        except Exception:
            pass


class _KeywordEmbedder:
    """简易关键词向量化器（API和本地模型都不可用时降级使用）"""
    def encode(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        import numpy as np
        return np.array([self._vec(t) for t in texts])

    def _vec(self, text, dim=384):
        import numpy as np
        vec = np.zeros(dim, dtype=np.float32)
        for i, ch in enumerate(text):
            vec[ord(ch) % dim] += 1
            if i < len(text) - 1:
                vec[(ord(ch) * 31 + ord(text[i + 1])) % dim] += 0.5
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec