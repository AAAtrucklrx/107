"""
小蜗 — 向量存储管理模块
基于 ChromaDB 的 FAQ 知识库检索
支持: OpenAI兼容API Embedding / 本地 SentenceTransformer / 关键词fallback
检索: 向量 + BM25 混合检索（RRF 融合）
"""

import math
import os
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from config import CHROMA_PERSIST_DIR, EMBEDDING_CONFIG, FAQ_TOP_K, FAQ_SIMILARITY_THRESHOLD, LLM_CONFIG
from utils.logger import get_logger

log = get_logger("xiaowo.vector")

# 不同 embedding 模式下的相似度阈值
# api 模式按 qwen3-embedding(4096维)实测校准：相关命中 0.45+，不相关 ≤0.38
THRESHOLD_MAP = {"api": 0.42, "local": 0.35, "fallback": 0.25}

RRF_K = 60

# P3-2：进程级嵌入器缓存（FAQVectorStore 非单例，避免多实例重复探测/加载模型）
_EMBEDDER_CACHE: dict = {}


def shared_embedder() -> tuple[Any, str]:
    """共享 embedder（encode 接口）+ 检索模式；供语义缓存等复用，不建 Chroma collection。

    复用模块级 _EMBEDDER_CACHE 单例，与 FAQVectorStore 的检索使用同一向量空间。"""
    shim = FAQVectorStore.__new__(FAQVectorStore)  # 跳过 __init__（不建 collection）
    model = shim._init_embedding()
    return model, shim._embed_method


class FAQVectorStore:
    """FAQ 知识库向量存储"""

    def __init__(self, persist_dir: str = CHROMA_PERSIST_DIR):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        self.collection_name = "faq_knowledge"
        self._embedding_model = None
        self._embed_method = None
        self._bm25_index = None
        self._bm25_ids: list[str] = []

        try:
            self.client = chromadb.PersistentClient(
                path=persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._init_collection()
        except Exception as e:
            log.error(f"ChromaDB 初始化失败，已保留原数据: {e}")
            raise RuntimeError(
                "ChromaDB 初始化失败，原索引未被删除。"
                "请先排查文件占用或权限；确认索引损坏后运行 "
                "rebuild_kb.py --yes 显式重建。"
            ) from e

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
        # P3-2：嵌入器模块级缓存（FAQVectorStore 非单例，历史上每个实例都重新
        # 探测 API —— 断网时每次探测 ~21s 防火墙丢包超时，一问可撞多次）
        cached = _EMBEDDER_CACHE.get("shared")
        if cached is not None:
            self._embed_method = cached[1]
            return cached[0]

        from utils.llm_client import llm_circuit_open

        def _cache(model, method):
            _EMBEDDER_CACHE["shared"] = (model, method)
            self._embed_method = method
            return model

        # 模式开关：XIAOWO_EMBEDDING_MODE = auto（默认，探针优先）| api 强制 | local 强制
        _mode = os.getenv("XIAOWO_EMBEDDING_MODE", "auto").strip().casefold()

        # 策略1: OpenAI 兼容 API（熔断窗内直接跳过探测）
        if _mode == "local":
            _use_api = False
        elif _mode == "api":
            _use_api = True
        else:
            _use_api = not llm_circuit_open() and self._probe_embedding_api()
        if _use_api:
            from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
            ef = OpenAIEmbeddingFunction(
                api_key=LLM_CONFIG["api_key"],
                api_base=LLM_CONFIG["base_url"],
                model_name=EMBEDDING_CONFIG["api_model"],
            )
            log.info("Embedding: API 模式")
            return _cache(_APIEmbedder(ef), "api")

        # 策略2: 本地 SentenceTransformer
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(
                EMBEDDING_CONFIG["model_name"],
                device=EMBEDDING_CONFIG["device"],
            )
            log.info("Embedding: 本地模型模式")
            return _cache(model, "local")
        except Exception as e2:
            log.warning(f"Embedding 本地模型不可用: {e2}")

        # 策略3: 关键词 fallback
        log.info("Embedding: 关键词 fallback 模式")
        return _cache(_KeywordEmbedder(), "fallback")

    @staticmethod
    def _probe_embedding_api(timeout: float = 5.0) -> bool:
        """API 可用性探测（短超时、不重试），替代直接 ef(["test"]) 的长撞墙。

        仅连接级失败（平台不可达）才开熔断窗；读超时等瞬时失败只降级本进程
        的 embedding 模式选择，不殃及后续 LLM 调用。
        """
        try:
            import openai
            client = openai.OpenAI(
                base_url=LLM_CONFIG["base_url"],
                api_key=LLM_CONFIG["api_key"],
                timeout=timeout,
                max_retries=0,
            )
            client.embeddings.create(model=EMBEDDING_CONFIG["api_model"], input=["test"])
            return True
        except Exception as e:
            from utils.llm_client import mark_llm_down_if_unreachable
            mark_llm_down_if_unreachable(e)
            log.warning(f"Embedding API 不可用（{timeout}s 探测失败）: {e}")
            return False

    def _collection_dim(self) -> int | None:
        """现有集合的 embedding 维度（无数据返回 None）。"""
        try:
            got = self.collection.get(limit=1, include=["embeddings"])
            embs = got.get("embeddings") or []
            if embs and embs[0] is not None:
                return len(embs[0])
        except Exception:
            pass
        return None

    def add_documents(self, documents: list[dict]):
        if not documents:
            return
        ids = []
        for d in documents:
            meta = d["metadata"] or {}
            if "chunk_index" in meta:
                ids.append(f"{d['id']}_chunk{meta['chunk_index']}")
            else:
                ids.append(d["id"])
        contents = [d["content"] for d in documents]
        metadatas = [d["metadata"] for d in documents]
        embeddings = self.embedding_model.encode(contents).tolist()
        # 维度冲突守卫：集合已存在且维度与当前 embedder 不一致时拒绝写入（避免混合维度集合）
        cdim = self._collection_dim()
        qdim = len(embeddings[0])
        if cdim is not None and cdim != qdim:
            raise RuntimeError(
                f"Embedding 维度冲突（集合 {cdim}D vs 当前 {qdim}D），"
                f"请运行 rebuild_kb.py --yes 全量重建后重试")
        self.collection.add(
            ids=ids, embeddings=embeddings,
            documents=contents, metadatas=metadatas,
        )
        self._invalidate_bm25()

    def _bm25_only_search(self, query: str, top_k: int, reason: str = "") -> dict:
        """向量检索不可用时的 BM25-only 降级检索（P3-2）。

        BM25 索引建立在集合存储文档上，与向量维度无关；结果带
        search_mode="bm25" 与 message 说明，调用方可据此标注降级来源。
        """
        try:
            bm25 = self._get_bm25_index()
            tokens = _tokenize_cjk(query)
            if bm25 is None or not tokens:
                return {"found": False, "results": [], "top_score": 0.0,
                        "mismatch": True, "message": reason or "向量检索不可用且无 BM25 索引"}
            scored = sorted(zip(self._bm25_ids, bm25.get_scores(tokens)), key=lambda kv: -kv[1])
            positive = [doc_id for doc_id, s in scored if s > 0]
            ranked = (positive or [doc_id for doc_id, _ in scored])[:top_k]
            if not ranked:
                return {"found": False, "results": [], "top_score": 0.0,
                        "mismatch": True, "message": reason}
            fetched = self.collection.get(ids=ranked)
            id_to_doc = dict(zip(fetched["ids"], fetched["documents"]))
            id_to_meta = dict(zip(fetched["ids"], fetched["metadatas"])) if fetched["metadatas"] else {}
            score_map = dict(scored)
            results = []
            for doc_id in ranked:
                meta = id_to_meta.get(doc_id) or {}
                results.append({
                    "id": doc_id,
                    "content": id_to_doc.get(doc_id, ""),
                    "score": round(float(score_map.get(doc_id, 0.0)), 4),
                    "source": meta.get("source", "未知来源"),
                    "category": meta.get("category", "其他"),
                    "subcategory": meta.get("subcategory", ""),
                    "is_official": meta.get("is_official", True),
                    "title": meta.get("title", ""),
                })
            return {"found": bool(positive), "results": results,
                    "top_score": results[0]["score"] if results else 0.0,
                    "search_mode": "bm25", "message": reason}
        except Exception as e:
            log.warning(f"BM25-only 降级检索失败: {e}")
            return {"found": False, "results": [], "top_score": 0.0, "mismatch": True}

    def search(self, query: str, top_k: int = FAQ_TOP_K) -> dict:
        if not query.strip() or self.collection.count() == 0:
            return {"found": False, "results": [], "top_score": 0.0}

        count = self.collection.count()
        # ① 召回池扩大(top_k*5): 多召回给 rerank 精排留空间, 提升 top 命中
        pool = min(max(top_k * 5, top_k + 5), count)

        # 1) 向量候选
        query_embedding = self.embedding_model.encode([query]).tolist()
        qdim = len(query_embedding[0])
        cdim = self._collection_dim()
        if cdim is not None and cdim != qdim:
            # 维度不匹配（如 API embedding 建的 4096D 集合遇本地 768D 降级）：
            # P3-2 起降级为 BM25-only 检索（索引建立在集合文档上，与向量维度无关），
            # 不再直接返回空结果——断网/维度不匹配时知识库仍可关键词检索
            log.warning(
                f"Embedding 维度不匹配（集合 {cdim}D vs 当前 {qdim}D），"
                f"降级为 BM25 关键词检索（如需恢复语义检索请运行 rebuild_kb.py --yes 重建）")
            return self._bm25_only_search(
                query, top_k, reason=f"向量检索不可用（维度不匹配 {cdim}D/{qdim}D），已降级为关键词检索")
        try:
            raw = self.collection.query(
                query_embeddings=query_embedding,
                n_results=pool,
            )
        except Exception as e:
            if "dimension" in str(e).lower():
                log.warning(
                    f"Embedding 维度不匹配（查询失败），降级为 BM25 关键词检索: {e}")
                return self._bm25_only_search(
                    query, top_k, reason="向量检索不可用（查询失败），已降级为关键词检索")
            raise
        vec_ids: list[str] = []
        vec_scores: dict[str, float] = {}
        if raw["ids"] and raw["ids"][0]:
            for i in range(len(raw["ids"][0])):
                doc_id = raw["ids"][0][i]
                vec_ids.append(doc_id)
                vec_scores[doc_id] = 1 - raw["distances"][0][i]

        # 2) BM25 候选
        bm_ranking: list[str] = []
        try:
            bm25 = self._get_bm25_index()
            query_tokens = _tokenize_cjk(query)
            if bm25 is not None and query_tokens:
                scored = list(zip(self._bm25_ids, bm25.get_scores(query_tokens)))
                scored.sort(key=lambda kv: -kv[1])
                # 过滤：保留正分块；负分块仅当池内正分块不足时兜底补入（负分多为命中
                # 高频虚词所致，如名单类块，绝对值无意义；直接剔除会让名单块永远检索不到）
                bm_ranking = [doc_id for doc_id, s in scored[:pool] if s > 0]
                if len(bm_ranking) < pool:
                    bm_ranking += [doc_id for doc_id, s in scored[:pool] if s <= 0][: pool - len(bm_ranking)]
        except Exception as e:
            log.warning(f"BM25 检索失败，仅用向量检索: {e}")

        # 3) RRF 融合（k=60），以融合排名决定最终顺序
        fused = _rrf_merge([vec_ids, bm_ranking], k=RRF_K)
        fused_ranked = [doc_id for doc_id, _ in
                        sorted(fused.items(), key=lambda kv: -kv[1])]
        ranked_ids = fused_ranked[:top_k]

        # 4) 可选 rerank 精排: 交叉编码器对 RRF 前 N 候选重排(模型缺失时静默回退)
        try:
            from knowledge.reranker import RERANK_POOL, rerank, rerank_available
            rerank_candidates = fused_ranked[:RERANK_POOL]
            if rerank_available() and len(rerank_candidates) > top_k:
                fetched_pre = self.collection.get(ids=rerank_candidates)
                doc_map = dict(zip(fetched_pre["ids"], fetched_pre["documents"]))
                doc_texts = [str(doc_map.get(doc_id, ""))[:300] for doc_id in rerank_candidates]
                top_idx = rerank(query, doc_texts, top_k)
                ranked_ids = [rerank_candidates[i] for i in top_idx]
                log.info(f"rerank 精排: {len(rerank_candidates)} 候选 → top{top_k}")
        except Exception as e:
            log.warning(f"rerank 精排跳过, 使用 RRF 顺序: {e}")

        results = []
        top_score = 0.0
        if ranked_ids:
            fetched = self.collection.get(ids=ranked_ids)
            id_to_doc = dict(zip(fetched["ids"], fetched["documents"]))
            id_to_meta = dict(zip(fetched["ids"], fetched["metadatas"])) if fetched["metadatas"] else {}
            for doc_id in ranked_ids:
                score = vec_scores.get(doc_id, 0.0)
                top_score = max(top_score, score)
                meta = id_to_meta.get(doc_id) or {}
                results.append({
                    "id": doc_id,
                    "content": id_to_doc.get(doc_id, ""),
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
        self._invalidate_bm25()

    # ── BM25 混合检索支持 ──────────────────────────────

    def _invalidate_bm25(self):
        self._bm25_index = None
        self._bm25_ids = []

    def _get_bm25_index(self):
        """构建/复用 BM25 索引（优先 rank_bm25，失败用内置实现）"""
        if self._bm25_index is None:
            data = self.collection.get()
            self._bm25_ids = data["ids"]
            corpus = [_tokenize_cjk(doc or "") for doc in data["documents"]]
            try:
                from rank_bm25 import BM25Okapi
                self._bm25_index = BM25Okapi(corpus)
            except ImportError:
                log.info("rank_bm25 不可用，使用内置 BM25")
                self._bm25_index = _BuiltinBM25(corpus)
        return self._bm25_index


def _rrf_merge(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion：各检索器排名按 1/(k+rank) 累加融合"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _tokenize_cjk(text: str) -> list[str]:
    """中文检索词切分：单字 + CJK bigram；非中文字母数字按词保留"""
    tokens = []
    prev = ""
    word = ""
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if word:
                tokens.append(word.lower())
                word = ""
            if prev:
                tokens.append(prev + ch)
            prev = ch
            tokens.append(ch)
        else:
            prev = ""
            if ch.isalnum():
                word += ch
            else:
                if word:
                    tokens.append(word.lower())
                    word = ""
    if word:
        tokens.append(word.lower())
    return tokens


class _BuiltinBM25:
    """内置 BM25 兜底实现（接口兼容 rank_bm25.BM25Okapi）"""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / self.corpus_size if corpus else 0.0
        self.doc_freqs = []
        self.doc_len = []
        df: dict[str, int] = {}
        for doc in corpus:
            freq = {}
            for term in doc:
                freq[term] = freq.get(term, 0) + 1
                df[term] = df.get(term, 0) + 1
            self.doc_freqs.append(freq)
            self.doc_len.append(len(doc))
        # +1 平滑保证 idf 恒正，避免负 idf 惩罚高频词
        self.idf = {term: math.log(1 + (self.corpus_size - n + 0.5) / (n + 0.5))
                    for term, n in df.items()}

    def get_scores(self, query: list[str]) -> list[float]:
        scores = []
        for i in range(self.corpus_size):
            score = 0.0
            doc_freq = self.doc_freqs[i]
            doc_len = self.doc_len[i]
            for term in query:
                if term not in self.idf or term not in doc_freq:
                    continue
                f = doc_freq[term]
                score += (self.idf[term] * f * (self.k1 + 1)
                          / (f + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)))
            scores.append(score)
        return scores


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


class _APIEmbedder:
    """OpenAI 兼容 API embedding 适配器：统一 encode() 接口，兼容本地模型与关键词 fallback"""

    def __init__(self, ef):
        self._ef = ef

    def encode(self, texts):
        import numpy as np
        if isinstance(texts, str):
            texts = [texts]
        return np.array(self._ef(texts), dtype=np.float32)


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
