"""
小蜗 — 用户意图分类器
12 类常见意图，基于示例句向量相似度分类（复用知识库嵌入模型，示例向量缓存）
嵌入模型不可用时降级为关键词匹配
意图定义见 agents/qa/intents.py（单一事实来源）
"""

import numpy as np
from utils.logger import get_logger

from agents.qa.intents import INTENTS

log = get_logger("xiaowo.intent")

_TOP3_SCORE_FLOOR = 1e-9


class IntentClassifier:
    """示例句向量相似度分类器"""

    def __init__(self, embedder=None):
        # embedder 需提供 encode(texts) -> np.ndarray；None 时复用 FAQVectorStore 的嵌入模型
        self._embedder = embedder
        self._example_vectors = None

    def _get_embedder(self):
        if self._embedder is None:
            from knowledge.vector_store import FAQVectorStore
            self._embedder = FAQVectorStore().embedding_model
        return self._embedder

    def _cached_example_vectors(self) -> np.ndarray:
        if self._example_vectors is None:
            examples = [ex for exs in INTENTS.values() for ex in exs]
            self._example_vectors = np.asarray(
                self._get_embedder().encode(examples), dtype=np.float32
            )
            norms = np.linalg.norm(self._example_vectors, axis=1, keepdims=True)
            self._example_vectors = self._example_vectors / (norms + _TOP3_SCORE_FLOOR)
        return self._example_vectors

    def classify(self, text: str, top_n: int = 3) -> dict:
        text = (text or "").strip()
        if not text:
            return {"intent": "闲聊", "top3": [], "method": "empty"}
        try:
            return self._classify_by_embedding(text, top_n)
        except Exception as e:
            log.warning(f"意图嵌入分类失败，降级为关键词匹配: {e}")
            return self._classify_by_keyword(text, top_n)

    def _classify_by_embedding(self, text: str, top_n: int) -> dict:
        query_vec = np.asarray(self._get_embedder().encode([text]), dtype=np.float32)[0]
        query_vec = query_vec / (np.linalg.norm(query_vec) + _TOP3_SCORE_FLOOR)
        sims = self._cached_example_vectors() @ query_vec

        scores: dict[str, float] = {}
        offset = 0
        for intent, examples in INTENTS.items():
            scores[intent] = float(np.max(sims[offset:offset + len(examples)]))
            offset += len(examples)

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return {
            "intent": ranked[0][0],
            "top3": [{"intent": k, "score": round(v, 4)} for k, v in ranked[:top_n]],
            "method": "embedding",
        }

    def _classify_by_keyword(self, text: str, top_n: int) -> dict:
        from knowledge.vector_store import _tokenize_cjk
        query_tokens = set(_tokenize_cjk(text))
        scores: dict[str, int] = {}
        for intent, examples in INTENTS.items():
            scores[intent] = max(
                len(query_tokens & set(_tokenize_cjk(ex))) for ex in examples
            )
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return {
            "intent": ranked[0][0],
            "top3": [{"intent": k, "score": v} for k, v in ranked[:top_n]],
            "method": "keyword",
        }


_default_classifier: IntentClassifier | None = None


def classify(text: str, top_n: int = 3) -> dict:
    """模块级便捷入口：复用共享实例（复用知识库嵌入模型，示例向量只算一次）"""
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = IntentClassifier()
    return _default_classifier.classify(text, top_n)


if __name__ == "__main__":
    for q in ["学生证怎么补办", "食堂几点关门", "密码忘了", "丢东西了"]:
        print(q, "->", classify(q))
