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
    "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
    "temperature": 0.3,
    # 输出预算:实测 2048 在长列表场景被截断(finish=length),4096 足够且自然收尾;
    # 平台接受 8192,但生成时延 ~30s,撞 45s 生成超时的风险更高
    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "4096")),
    "timeout": 30,
    # 平台故障快速失败：0＝不重试，1＝重试一次（最多 2 次尝试）；P3-2 降级路由配套
    "max_retries": int(os.getenv("LLM_MAX_RETRIES", "1")),
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
# chromadb 1.5.9 的 Rust 端在中文/非 ASCII 路径下无法落盘 hnsw 索引
# (data_level0.bin 等文件静默缺失 → 重建后新进程打开报 "Error loading hnsw index")。
# 因此持久化目录必须指向纯英文物理路径(默认 C:\xiaowo_kb\chroma_db,env 可覆盖)。
CHROMA_PERSIST_DIR = os.getenv("XIAOWO_CHROMA_DIR", r"C:\xiaowo_kb\chroma_db")
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
# 青春科大个人快照路径（crawl_young.py 写入，activity_profile/activity_tools 读取，单一来源）
YOUNG_SNAPSHOT_PATH = PROJECT_ROOT / "scripts" / "data" / "young_personal" / "young_snapshot.json"

# ── 学期常量（每学期开学前只更新这一处；来源：教务处校历 teach.ustc.edu.cn/calendar）──
# start_date 必须为该学期第一个周一；schedule_tools.import_schedule 等据此对齐课表星期
SEMESTER = {
    "name": "2026-2027-1",
    "start_date": os.getenv("XIAOWO_SEMESTER_START", "2026-08-31"),
    "total_weeks": 18,
}