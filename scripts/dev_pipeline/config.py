"""小蜗开发流水线 — 配置模块。

从项目 .env 与系统环境读取配置，集中管理路径、LLM、执行器。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---- 路径 ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # F:\小蜗
PIPELINE_DIR = Path(__file__).resolve().parent
CANVAS_DIR = PROJECT_ROOT / ".qoder" / "canvases"
REPORT_DIR = PROJECT_ROOT / "docs" / "pipeline"

# ---- 后端（中科大 dsV4flash）----
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.llm.ustc.edu.cn/v1")
LLM_API_KEY = os.environ.get("USTC_API_KEY") or os.environ.get("LLM_API_KEY") or ""
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

# ---- 执行器 CLI ----
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", r"C:\claude\claude.exe")
CODEX_BIN = os.environ.get("CODEX_BIN", r"C:\Users\Richelieu\AppData\Local\OpenAI\Codex\bin\cfac6bda2d141e07\codex.exe")

# ---- 执行参数 ----
DEFAULT_MAX_ROUNDS = 3          # 决策→执行循环上限
EXEC_TIMEOUT_SEC = 600          # 单轮执行超时
LLM_TIMEOUT_SEC = 120

# ---- 测试入口（按顺序探测，找到即用）----
TEST_SCRIPTS = [
    PROJECT_ROOT / "scripts" / "test_fixes.py",
    PROJECT_ROOT / "scripts" / "grade_match_test.py",
    PROJECT_ROOT / "scripts" / "verify_programs.py",
]

EXCLUDED_COMPILE_DIRS = {
    "__pycache__", ".git", ".qoder", ".venv", "node_modules", "data", "database",
}


def ensure_dirs() -> None:
    CANVAS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
