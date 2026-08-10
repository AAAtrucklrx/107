# 小蜗开发流水线 · Codex 执行器使用指南

本文说明如何用 Codex 执行器运行小蜗项目（`F:\小蜗`）的自动化开发流水线。
流水线模块位于 `scripts/dev_pipeline/`（CLI 入口为 `cli.py` / `__main__.py`），
初始化校验脚本为项目根目录 `init_check.py`。以下命令均在 `F:\小蜗` 项目根目录执行。

## 1. 前置条件

- 安装 Python 3.11+，并先完成「构建」阶段的依赖安装
- LLM 配置：`USTC_API_KEY` 或 `LLM_API_KEY`（可写入项目 `.env`），默认调用中科大 `deepseek-v4-flash`
- 执行器配置：`CODEX_BIN` 环境变量可覆盖 Codex 可执行文件路径，默认为
  `C:\Users\Richelieu\AppData\Local\OpenAI\Codex\bin\cfac6bda2d141e07\codex.exe`（见 `scripts/dev_pipeline/config.py`）

## 2. 阶段命令示例

### 2.1 初始化

运行项目初始化校验：初始化数据库、导入种子数据、构建知识库向量索引。

```powershell
cd F:\小蜗
python init_check.py
```

### 2.2 构建

安装流水线依赖：

```powershell
cd F:\小蜗
python -m pip install -r scripts\dev_pipeline\requirements.txt
```

### 2.3 运行（Codex 执行器驱动开发闭环）

```powershell
cd F:\小蜗
python -m scripts.dev_pipeline run "开发任务描述" --executor codex --rounds 3
```

- `--executor codex`：使用 Codex 执行器（danger-full-access 权限）按流水线计划修改代码
- `--rounds`：决策→执行循环上限，默认 3
- `--json`：额外输出 JSON 状态（verdict / issues / 报告路径等）
- 退出码：`0` = 验收通过；`1` = 未通过（保留问题报告）

### 2.4 运行（查看状态与报告）

```powershell
cd F:\小蜗
python -m scripts.dev_pipeline status
```

输出项目根目录、`docs\pipeline\`（Markdown 报告）与 `.qoder\canvases\`（Canvas 报告）的最新文件清单。

## 3. 产物位置

- Markdown 报告：`F:\小蜗\docs\pipeline\`
- Canvas 报告：`F:\小蜗\.qoder\canvases\pipeline-*.canvas.tsx`

> 说明：流水线的计划/执行/测试/报告/决策各节点由 `scripts/dev_pipeline/` 模块自动编排。
> 本文仅说明如何用 Codex 执行器触发与查看运行结果，不涉及具体业务逻辑。
