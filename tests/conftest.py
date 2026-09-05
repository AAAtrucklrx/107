import os

# 测试环境：HF 离线（避免 prewarm 触发模型下载重试拖慢/挂起测试）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
