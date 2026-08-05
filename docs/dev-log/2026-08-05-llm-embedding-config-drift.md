# 2026-08-05 llm-embedding-config-drift

## 目标

finding: `llm-embedding-config-drift`。
- 统一 LLM/Embedding 配置读取来源（`.env` 为单一事实源，代码不再硬编码模型标识）
- 使声明模型与实测可用能力一致（qwen3-embedding 实测通过）

## 改动文件

- `config.py`：`LLM_CONFIG["base_url"]`/`["model"]` 改由 `LLM_BASE_URL`/`LLM_MODEL` 覆盖（默认值分别对齐 .env 与实测值 deepseek-chat）；`EMBEDDING_CONFIG` 新增 `api_model`（默认 qwen3-embedding，可由 `LLM_EMBEDDING_MODEL` 覆盖）
- `knowledge/vector_store.py`：
  - 硬编码 `text-embedding-3-small` 改为 `EMBEDDING_CONFIG["api_model"]`
  - 新增 `_APIEmbedder` 适配器：`OpenAIEmbeddingFunction` 无 `encode()`，API 模式此前必然 AttributeError（被 403 掩盖的 latent bug），现统一 encode 接口
  - `THRESHOLD_MAP["api"]` 0.6 → 0.42：按 qwen3-embedding（4096 维）实测校准（相关命中 0.45+，不相关 ≤0.38）

## 验证

- 配置读取：`model=deepseek-chat`、`api_model=qwen3-embedding`、api_key 已注入：通过
- embedding 实测：`POST /v1/embeddings` model=qwen3-embedding → **200，4096 维**：通过
- 重建索引（dimension 768→4096 不兼容）：`py rebuild_kb.py --yes` 成功，备份 `knowledge_backup_20260805_225512`（同时验证修复 4 的备份功能）
- 检索相关性探查：6 个 query top1 全部命中正确文档；阈值校准后 `py init_check.py` 全链路通过（Found=True）
- 首次运行暴露既有 bug：API 模式 `search()` 必然 AttributeError + 768/4096 维度冲突，已随本修复解决

## 遗留问题

- 阈值 0.42 基于 6 个样本校准，若后续 FAQ 文档扩充建议复查
- 本地 SentenceTransformer 模型路径未实测（API 可用时不会走到）；`knowledge_backup_20260805_225512` 为旧 768 维索引备份，确认无需恢复后可手动清理（该目录已被 gitignore）
