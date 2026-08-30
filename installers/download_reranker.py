# -*- coding: utf-8 -*-
"""下载 bge-reranker-base-onnx 到 C:\\xiaowo_kb\\models(经 hf-mirror)。
用法: .venv\\Scripts\\python.exe installers/download_reranker.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # 镜像 xet 存储 401, 强制 LFS 直链
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

TARGET = Path(r"C:\xiaowo_kb\models\bge-reranker-base")

if (TARGET / "onnx" / "model.onnx").is_file() or (TARGET / "model.onnx").is_file():
    print("模型已存在:", TARGET)
    sys.exit(0)

t0 = time.time()
from huggingface_hub import snapshot_download  # noqa: E402

print("开始下载 Xenova/bge-reranker-base ->", TARGET)
snapshot_download(
    "Xenova/bge-reranker-base",
    local_dir=str(TARGET),
)
print(f"下载完成, 耗时 {time.time() - t0:.0f}s")
for f in sorted(TARGET.iterdir()):
    print(" ", f.name, f.stat().st_size if f.is_file() else "(dir)")
