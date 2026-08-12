# 🐌 小蜗 — 科大校园智能助手

> 107 杯比赛项目 · 科大校园场景的 AI 智能体助手

小蜗是一个面向中国科学技术大学师生的校园智能助手，基于科大 LLM 平台（`api.llm.ustc.edu.cn`）构建，采用**统一 QA LangGraph** 智能体架构（意图分类 + 混合召回双通道 → LLM 自主决策循环 → 工具执行 → 统一回答），支持知识库问答与教务数据实时查询。

## ✨ 功能

| 模块 | 能力 |
|------|------|
| 📚 智能问答 | 基于 50 篇校园知识库文档（220 条向量分块）的 RAG 问答（办事指南、教务、生活、就业、科研与升学） |
| 📊 课业助手 | 成绩查询、课表查询、GPA 计算、空教室查询；CAS 登录后经 jw API 拉取**真实教务数据** |
| 🔍 选课顾问 | 基于 icourse.club 真实评课数据推荐（2351 门课程 / 4.4 万条评论）：真实均分降序、同课多师并列对比、5-6 条真实评论引用，支持画像软过滤与教师分析 |
| 📅 日程管理 | 日程记录与查询、空闲时间匹配、校团委活动推荐 |

所有降级/模拟数据均带有**来源标识**（实时数据 / 本地缓存 / 模拟数据），保证结果可追溯。

## 🛠 技术栈

- **前端/框架**: Streamlit（Web UI）
- **智能体**: LangGraph · 统一 QA 流程（embedding_parse → think 自主决策 ≤4 轮 → act → compose）
- **知识库**: ChromaDB 向量库 + SentenceTransformer / qwen3-embedding
- **数据**: SQLite 双库——`database/xiaowo.db`（主库，含 schema 与 seed）+ `data/course_data.db`（评课库，8 表，schema 见 `database/schema_course.sql`，由 `scripts/build_course_db.py` 构建）
- **外部服务**: 科大统一身份认证（CAS）、教务系统（jw API）、科大 LLM 平台

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（复制 .env.example 为 .env 并填写）
#    LLM_API_KEY=你的科大 LLM 平台密钥
#    LLM_MODEL=deepseek-chat
#    LLM_EMBEDDING_MODEL=qwen3-embedding

# 3. 构建评课库（选课推荐数据源；已构建则跳过）
python scripts/crawl_icourse.py all   # 全量抓取 icourse.club 评课（断点续爬，可选）
python scripts/build_course_db.py     # 构建 data/course_data.db（8 表）

# 4. 初始化检查（自动建库 + 知识库校验）
python init_check.py

# 5. 启动应用
streamlit run app.py
```

访问 `http://localhost:8501` 即可使用（未登录可体验知识库问答；课表/成绩/日程等个人数据需 CAS 登录后使用）。

> 注：应用内 CAS 登录跳转依赖 CAS service 白名单，本地开发使用 `http://localhost:8501`，部署后需配置实际域名。

## 📁 项目结构

```
├── agents/          # 智能体（qa/ 统一问答图 + 旧 router/planner/executor 等保留）
├── tools/           # 工具层（课程/成绩/课表/教室/日程/知识库检索）
├── services/        # 外部服务（CAS 登录、jw API、校团委 young 平台）
├── knowledge/       # 知识库文档（data/ 50 篇 md）与向量库
├── database/        # SQLite schema 与种子数据
├── ui/              # Streamlit 界面组件
├── docs/            # 项目文档（dev-log、工具规格、团队材料）
└── scripts/         # 数据采集与 seed 生成脚本（含真实 catalog 抓取记录）
```

## 📄 文档

- [docs/dev-log/](docs/dev-log/) — 开发过程记录（finding 修复日志）
- [docs/tool-specs.md](docs/tool-specs.md) — Tool 详细规格说明
- [docs/team/](docs/team/) — 团队协作材料（备赛计划、协作指南、智能体配置）

## ✅ 选课推荐全链路验收

选课推荐链路（用户提问 → 意图识别 → 工具推荐 → 综合回答）验收命令与结果（2026-08-13 v3）：

| 步骤 | 命令 | 结果 |
|------|------|------|
| 数据完整性校验 | `python scripts/check_course_db.py` | 9/9 通过（2351 门课程 / 44403 条评论 / 2981 位教师，无孤儿引用、无重复学期）；干净环境重建数据库行数完全一致 |
| 工具层冒烟测试 | `python scripts/verify_tools.py` | 17/17 断言通过（推荐排序 / 低 workload 筛选 / 教师对比，结果含来源标识） |
| 智能体链路冒烟测试 | `python scripts/advisor_smoke.py` | 5/5 通过（推荐 / 低 workload / 教师对比 / 澄清 / FAQ 均正确路由） |
| 浏览器端到端测试 | `python scripts/browser_e2e.py` | 6 场景全部通过（首页 + 5 类提问），截图见 `docs/e2e_v3_*.png` |
| 初始化回归 | `python init_check.py` | 数据库 + 知识库（50 篇文档 / 220 条向量）初始化正常 |
| 历史修复回归 | `python scripts/test_fixes.py` | 23/23 通过（排序 / 多师并列 / 评论去重 / 画像理由 / 数据对账） |
| 流水线验收 | `python -m scripts.dev_pipeline run "选课推荐全链路验收" --executor qoder` | PASS（1 轮），Canvas 报告见 `.qoder/canvases/` |

E2E 覆盖场景：首页、Q1 课程推荐（含评课参考）、Q2 低 workload 筛选、Q3 教师对比、Q4 澄清追问（信息不足时进入 clarify 分支）、Q5 FAQ 兜底（回答带《来源》标注）。评课数据来自 icourse.club 真实评论（`data/course_data.db`），教务模块降级数据均带"实时 / 本地缓存 / 模拟数据"来源标识。

### 已知限制

- 评课数据为 icourse.club 抓取快照，新开课程可能暂无评论；样本量过少的课程/教师评分仅供参考（推荐画像会附"样本较少"提示）
- 课程推荐依赖科大 LLM 平台进行意图分类与回答生成，平台不可用时无法出推荐结果
- 教师对比中样本量差异较大（1~553 条），均分排序存在小样本偏差，建议结合评论原文判断

## ⚖️ 说明

- 课程/课表/考试数据基于真实 catalog 抓取；成绩/课表/日程等个人数据登录后由 jw API 实时拉取，未登录时相关查询返回登录提示
- 敏感配置（`.env`）不进入版本库，密钥通过环境变量注入
