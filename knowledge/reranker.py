"""bge-reranker 交叉编码器精排(onnxruntime)。

模型来源: Xenova/bge-reranker-base(hf-mirror 下载到 XIAOWO_RERANKER_DIR)。
优先使用 int8 量化版(实测 24 候选推理 ~0.1s, 加载 ~3s); 缺失时回退 fp32。
模型缺失/加载失败/推理异常时自动禁用, 调用方回退 RRF 融合原始顺序(降级不报错)。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from utils.logger import get_logger

log = get_logger("xiaowo.rerank")

RERANKER_DIR = os.getenv("XIAOWO_RERANKER_DIR", r"C:\xiaowo_kb\models\bge-reranker-base")
# 精排候选上限(从 RRF 融合池中取前 N 再精排)与输入截断
RERANK_POOL = int(os.getenv("XIAOWO_RERANK_POOL", "24"))
RERANK_MAX_LEN = 256
RERANK_DOC_CHARS = 200

_RERANKER_CACHE: dict = {"session": None, "tokenizer": None, "failed": False}
_PREWARMED = threading.Event()


def _model_paths() -> tuple[Path | None, Path]:
    base = Path(RERANKER_DIR)
    for candidate in (
        base / "onnx" / "model_int8.onnx",  # int8 量化: 快 ~30 倍
        base / "onnx" / "model.onnx",
        base / "model_int8.onnx",
        base / "model.onnx",
    ):
        if candidate.is_file():
            return candidate, base
    return None, base


def rerank_available() -> bool:
    """模型可用(未失败且文件存在)才尝试精排, 避免每次检索都探测。"""
    if _RERANKER_CACHE["failed"]:
        return False
    model_path, _ = _model_paths()
    return model_path is not None


def _load():
    if _RERANKER_CACHE["session"] is not None:
        return _RERANKER_CACHE["session"], _RERANKER_CACHE["tokenizer"]
    if _RERANKER_CACHE["failed"]:
        return None, None
    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        model_path, base = _model_paths()
        if model_path is None:
            _RERANKER_CACHE["failed"] = True
            return None, None
        tokenizer = AutoTokenizer.from_pretrained(str(base))
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        _RERANKER_CACHE["session"] = session
        _RERANKER_CACHE["tokenizer"] = tokenizer
        _PREWARMED.set()
        log.info(f"reranker 已加载: {model_path.name}")
        return session, tokenizer
    except Exception as e:  # noqa: BLE001
        log.warning(f"reranker 加载失败, 本次会话禁用精排: {e}")
        _RERANKER_CACHE["failed"] = True
        return None, None


def prewarm() -> None:
    """后台预热(服务启动时调用, 不阻塞): 提前加载模型, 避免首次问答卡顿。"""
    if _PREWARMED.is_set() or _RERANKER_CACHE["failed"]:
        return
    threading.Thread(target=_load, name="reranker-prewarm", daemon=True).start()


def rerank(query: str, docs: list[str], top_k: int) -> list[int]:
    """对 docs 精排, 返回相关度降序的 top_k 个索引; 不可用时返回前 top_k 个索引。

    bge-reranker 输入为 [query, passage] 句对, 输出 logits, sigmoid 得相关分。
    """
    if not docs:
        return []
    if len(docs) <= top_k:
        return list(range(len(docs)))
    session, tokenizer = _load()
    if session is None:
        return list(range(top_k))
    try:
        import numpy as np

        pairs = [[query, doc[:RERANK_DOC_CHARS]] for doc in docs]
        enc = tokenizer(pairs, padding=True, truncation=True,
                        max_length=RERANK_MAX_LEN, return_tensors="np")
        feeds = {
            session.get_inputs()[0].name: enc["input_ids"],
            session.get_inputs()[1].name: enc["attention_mask"],
        }
        logits = session.run(None, feeds)[0].flatten()
        scores = 1.0 / (1.0 + np.exp(-logits))
        order = np.argsort(-scores)
        return [int(index) for index in order[:top_k]]
    except Exception as e:  # noqa: BLE001
        log.warning(f"rerank 推理失败, 回退 RRF 顺序: {e}")
        return list(range(top_k))
