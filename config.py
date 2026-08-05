"""
小蜗 — 配置管理模块
科大校园全能智能助手
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 加载 .env 文件
load_dotenv(PROJECT_ROOT / ".env")

# HuggingFace 镜像（解决国内下载问题）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# ── LLM 配置（base_url/model 可由 .env 覆盖，api_key 必读 .env）──────────────────
LLM_CONFIG = {
    "base_url": os.getenv("LLM_BASE_URL", "https://api.llm.ustc.edu.cn/v1"),
    "api_key": os.getenv("LLM_API_KEY", "your-api-key-here"),
    "model": os.getenv("LLM_MODEL", "deepseek-chat"),
    "temperature": 0.3,
    "max_tokens": 2048,
    "timeout": 30,
}

# ── Embedding 配置 ────────────────────────────
# api_model: 平台白名单内的 embedding 模型标识，可由 .env 的 LLM_EMBEDDING_MODEL 覆盖
EMBEDDING_CONFIG = {
    "model_name": "shibing624/text2vec-base-chinese",
    "api_model": os.getenv("LLM_EMBEDDING_MODEL", "qwen3-embedding"),
    "device": "cpu",
}

# ── 数据库配置 ────────────────────────────────
DATABASE_PATH = PROJECT_ROOT / "database" / "xiaowo.db"
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"

# ── 知识库配置 ────────────────────────────────
CHROMA_PERSIST_DIR = str(PROJECT_ROOT / "knowledge" / "chroma_db")
KNOWLEDGE_DATA_DIR = PROJECT_ROOT / "knowledge" / "data"
FAQ_TOP_K = 5
FAQ_SIMILARITY_THRESHOLD = 0.6  # 低于此分数认为无匹配

# ── CAS 统一认证配置 ──────────────────────────
# CAS 回调地址：CAS 认证完成后重定向回此地址
# 本地开发默认 localhost:8501，部署后改为实际域名
# 注意：此 URL 需要在 CAS 系统中注册为合法 service，否则会被拒绝
CAS_SERVICE_URL = os.getenv("CAS_SERVICE_URL", "http://localhost:8501")

# ── 校团委活动（young 平台）配置 ─────────────────
# 测试期：个人 token（young.ustc.edu.cn 登录后从浏览器 localStorage 提取，约7天有效）
# 远期：学校开放官方接口后切换白名单接入（YoungService 已预留 Provider 抽象）
YOUNG_TOKEN = os.getenv("YOUNG_TOKEN", "")
YOUNG_PAGE_SIZE = int(os.getenv("YOUNG_PAGE_SIZE", "50"))

# ── 演示学生配置 ──────────────────────────────
DEMO_STUDENT = {
    "id": "PB20240001",
    "name": "张同学",
    "major": "计算机科学",
    "grade": "大二",
    "semester": "2025-2026-2",
}