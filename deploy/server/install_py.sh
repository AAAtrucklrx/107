#!/usr/bin/env bash
# 小蜗 Linux 服务器 Python 依赖安装脚本（bwrap 沙箱内使用）
# 优先 requirements.lock.txt（163 个锁定版本），失败回退 requirements.txt
set -uo pipefail
cd "$(dirname "$0")/../.."

PY=python3
if [ ! -d .venv ]; then
  echo "[1/3] 创建 venv..."
  "$PY" -m venv .venv || { echo "venv 创建失败"; exit 1; }
fi
PIP=.venv/bin/pip

echo "[2/3] 升级 pip..."
"$PIP" install --upgrade pip -q

echo "[3/3] 安装依赖..."
if "$PIP" install -r requirements.lock.txt; then
  echo "LOCK INSTALL OK"
else
  echo "lock 安装失败，回退 requirements.txt"
  "$PIP" install -r requirements.txt || { echo "requirements.txt 也失败"; exit 1; }
fi

.venv/bin/python - <<'EOF'
import fastapi, chromadb, streamlit, langgraph, onnxruntime, huggingface_hub
print("关键 imports OK")
EOF
echo "DONE"
