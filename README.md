# 🐌 小蜗 — 科大校园智能助手

> 107 杯比赛项目 · 科大校园场景的 AI 智能体助手

小蜗是一个面向中国科学技术大学师生的校园智能助手，基于科大 LLM 平台（`api.llm.ustc.edu.cn`）构建，采用 **Plan-and-Execute** 智能体架构，支持知识库问答与教务数据实时查询。

## ✨ 功能

| 模块 | 能力 |
|------|------|
| 📚 智能问答 | 基于 50 篇校园知识库文档的 RAG 问答（办事指南、教务、生活、就业、科研与升学） |
| 📊 课业助手 | 成绩查询、课表查询、GPA 计算、空教室查询；CAS 登录后经 jw API 拉取**真实教务数据** |
| 🔍 选课顾问 | 基于课程目录（catalog）的真实课程数据推荐、评课参考 |
| 📅 日程管理 | 日程记录与查询、空闲时间匹配、校团委活动推荐 |

所有降级/模拟数据均带有**来源标识**（实时数据 / 本地缓存 / 模拟数据），保证结果可追溯。

## 🛠 技术栈

- **前端/框架**: Streamlit（Web UI）
- **智能体**: LangChain · Plan-and-Execute 架构（Router → Planner → Executor → Tools）
- **知识库**: ChromaDB 向量库 + SentenceTransformer / qwen3-embedding
- **数据**: SQLite（`database/xiaowo.db`，含 schema 与 seed）
- **外部服务**: 科大统一身份认证（CAS）、教务系统（jw API）、科大 LLM 平台

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（复制 .env.example 为 .env 并填写）
#    LLM_API_KEY=你的科大 LLM 平台密钥
#    LLM_MODEL=deepseek-chat
#    LLM_EMBEDDING_MODEL=qwen3-embedding

# 3. 初始化检查（自动建库 + 知识库校验）
python init_check.py

# 4. 启动应用
streamlit run app.py
```

访问 `http://localhost:8501` 即可使用（演示学生 `PB20240001` 未登录可体验演示数据）。

> 注：应用内 CAS 登录跳转依赖 CAS service 白名单，本地开发使用 `http://localhost:8501`，部署后需配置实际域名。

## 📁 项目结构

```
├── agents/          # 智能体（faq/course/advisor/schedule/planner/executor/router）
├── tools/           # 工具层（课程/成绩/课表/教室/日程/知识库检索）
├── services/        # 外部服务（CAS 登录、jw API、校团委 young 平台）
├── knowledge/       # 知识库文档（data/ 50 篇 md）与向量库
├── database/        # SQLite schema 与种子数据
├── ui/              # Streamlit 界面组件
├── docs/            # 项目文档（交接报告、成果说明书、技术架构、dev-log）
└── scripts/         # 数据采集与 seed 生成脚本（含真实 catalog 抓取记录）
```

## 📄 文档

- [项目交接报告.md](项目交接报告.md) — 项目全貌与交接信息
- [成果说明书.md](成果说明书.md) — 比赛成果说明
- [技术架构.md](技术架构.md) — 系统架构设计
- [docs/dev-log/](docs/dev-log/) — 开发过程记录（finding 修复日志）

## ⚖️ 说明

- 课程/课表/考试数据基于真实 catalog 抓取；成绩与评课在登录前为演示数据，登录后由 jw API 实时拉取
- 敏感配置（`.env`）不进入版本库，密钥通过环境变量注入
