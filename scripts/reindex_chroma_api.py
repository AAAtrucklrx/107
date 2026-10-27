#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把现有 Chroma 集合用 API embedding（text-embedding-v4, 1024 维）全量重嵌（2026-10-25）。

旧目录先备份为 `<chroma_dir>.bak_<日期>`，再用旧 id/文档/元数据重建同名集合。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb
from config import CHROMA_PERSIST_DIR
from knowledge.vector_store import shared_embedder

COLLECTION = "faq_knowledge"
BATCH = 10  # text-embedding-v4 单次最多 10 行


def main() -> int:
    dir_path = str(CHROMA_PERSIST_DIR)
    backup = f"{dir_path}.bak_{time.strftime('%Y%m%d')}"
    if not os.path.isdir(dir_path):
        print(f"❌ Chroma 目录不存在: {dir_path}")
        return 1
    if not os.path.isdir(backup):
        shutil.copytree(dir_path, backup)
        print(f"① 已备份: {backup}")
    client = chromadb.PersistentClient(path=dir_path)
    col = client.get_collection(COLLECTION)
    data = col.get(include=["documents", "metadatas"])
    ids = data["ids"]
    docs = data["documents"] or []
    metas = data["metadatas"] or [{} for _ in ids]
    print(f"② 旧集合 {COLLECTION}: {len(ids)} 条")

    model, method = shared_embedder()
    print(f"③ embedder 模式: {method}")

    client.delete_collection(COLLECTION)
    new = client.create_collection(COLLECTION)
    done = 0
    for i in range(0, len(docs), BATCH):
        batch_docs = docs[i:i + BATCH]
        vecs = model.encode(batch_docs)
        new.add(ids=ids[i:i + BATCH], documents=batch_docs, metadatas=metas[i:i + BATCH],
                embeddings=[[float(x) for x in v] for v in vecs])
        done += len(batch_docs)
        print(f"   重嵌 {done}/{len(docs)}")
    print(f"④ 新集合条数: {new.count()}")
    got = new.get(include=["embeddings"])
    embs = got.get("embeddings")
    dim = int(len(embs[0])) if embs is not None and len(embs) else 0
    print(f"⑤ 维度: {dim}（期望 1024）")
    return 0 if dim == 1024 and new.count() == len(ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
