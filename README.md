# 🐌 小蜗 — 科大校园智能助手

> 107 杯比赛项目 · 科大校园场景的 AI 智能体助手

小蜗是一个面向中国科学技术大学师生的校园智能助手，基于科大 LLM 平台（`api.llm.ustc.edu.cn`）构建，采用**统一 QA LangGraph** 智能体架构（意图分类 + 混合召回双通道 → LLM 自主决策循环 → 工具执行 → 统一回答），支持知识库问答与教务数据实时查询。

## ✨ 功能

| 模块 | 能力 |
|------|------|
| 📚 智能问答 | 基于 80 篇校园知识库文档（769 条向量分块）的 RAG 问答（办事、教务、生活、就业、科研与升学），回答可附官方来源网址；平台故障时自动降级 BM25 关键词检索 |
| 🌐 联网证据 | 本地资料不足、权威性不足或问题要求“最新”时，经隐私清洗后使用 SearXNG + Crawl4AI 保底；达到一手权威来源或双独立可靠来源门槛后才输出确定结论 |
| 📊 课业助手 | 成绩查询、课表查询（节次精确到分钟）、GPA 计算、空教室查询；CAS 登录后经 jw API 拉取**真实教务数据** |
| 🔍 选课顾问 | 基于 icourse.club 真实评课数据（5667 个课程页 / 4.4 万条评论）与培养方案，按“必修 / 方案内选修 / 方向补充”推荐；本轮需求优先，课程范围和“只要/必须”是硬条件，其余偏好默认软排序；独立提供节次/周次级课表冲突检查与退补选压力评估 |
| 🧭 培养方案 | 登录后只使用当前用户 CAS/成绩档案中的专业、年级和个人方案；个人方案不可用时可按已验证身份显示醒目标注的“专业通用参考，不是个人培养方案”，不会继承登录前的匿名预览选择 |
| 📅 日程管理 | 日程记录与查询（自然语言时间解析："明天下午3点到4点"精确落点）、冲突检测、课表导入 |
| 🎪 活动推荐 | **青春科大（第二课堂）实时数据**：对话直接查报名中活动；每日弹窗四因子个性化推荐（紧迫度/课表空闲/个性化/热度），个性化=平台兴趣标签+行为学习+**德智体美劳模块学时均衡补短板**；token 失效自动回退本地快照 |
| 🔗 官方入口 | 强操作类诉求（选退课/评教/缴费等）给出**已核实**官方入口跳转 + 小蜗辅助（冲突检测/压力模拟）；校园服务工作区以 8 个配置驱动的高频方块和分类目录呈现 19 条官方工具与网站 |
| 🧩 生态工具 | 学生自制工具投稿接入（Spec + 函数三步，`eco:` 前缀强制署名，见 `tools/ecosystem/README.md`） |
| 🛡 可靠性 | LLM 平台断网/变慢三层降级：不假死、工具摘要直出、熔断不传染 |
| 🧾 知识审核 | 回答完成后异步清洗公开证据；审核者逐块明确批准或排除，随后以不可变 Chroma/BM25 generation 原子发布；任务合并与 embedding 缓存减少重复工作 |

所有降级/缓存数据均带有**来源标识**（实时数据 / 本地缓存 / 第三方工具），保证结果可追溯。

## 🛠 技术栈

- **Web 应用**: React + Vite + TypeScript 四工作区前端，采用“冷色数字编目台”设计系统；FastAPI 模块化单体后端，SQLite 持久事件 + 进程内通知驱动的 SSE 流式状态与回答
- **过渡界面**: Streamlit 在迁移验收后的一个版本内只作为回退入口，不再并行发展为第二套生产 UI
- **智能体**: LangGraph · 统一 QA 流程（embedding_parse → think 自主决策 ≤4 轮（规则 1-22）→ act → compose；确定性路由兜底）
- **知识库**: ChromaDB 混合检索（向量 + BM25，维度不匹配自动降级 BM25-only）
- **数据**: SQLite 应用库 + 审核库 + 评课库，Chroma/BM25 generation，公开网页快照与批准数据全部位于 gitignore 数据目录
- **外部服务**: 科大统一身份认证（CAS）、教务系统（jw API）、青春科大 young、科大 LLM 平台；联网 sidecar 为 SearXNG 与 Crawl4AI adapter

## 🚀 快速开始

```powershell
# 1. Python 与前端依赖
pip install -r requirements.txt
Set-Location frontend
npm ci
npm run build
Set-Location ..

# 2. 复制 .env.example 为 .env；开发默认使用 anonymous，联网默认关闭。
#    构建后的 SPA 由 FastAPI 同源托管，因此将 XIAOWO_PUBLIC_ORIGIN 改为：
#    XIAOWO_PUBLIC_ORIGIN=http://localhost:8000

# 3. 初始化现有数据，再启动 Web 应用
python init_check.py
python -m uvicorn xiaowo_web.main:app --host 127.0.0.1 --port 8000
```

访问 `http://localhost:8000`。前后端分离开发时保留 `.env.example` 的 `http://localhost:5173`，另一个终端在 `frontend/` 运行 `npm run dev`。

比赛演示使用 `XIAOWO_ENV=competition`、`XIAOWO_AUTH_MODE=demo` 和唯一合成身份 `PB25111691`；demo 管理员只能发布到 demo 索引。无 HTTPS 域名的公开 production 只能使用 anonymous，个人区和管理区均关闭。真实 CAS 只保留 Provider/API，必须等可信 HTTPS 来源与 CAS service 白名单就绪后启用。

SearXNG、Crawl4AI sidecar、worker、数据包迁移和 generation 回滚见 [Web 部署与数据迁移](docs/Web部署与数据迁移.md)。当前开发机不安装或启动这些 sidecar。旧 Streamlit 回退入口仍可用 `python -m streamlit run app_test.py --server.port 8502` 启动。

## 📁 项目结构

```
├── agents/          # 智能体（qa/ 统一问答图 + tool_registry 28 内置工具注册表）
├── tools/           # 工具层（课程/成绩/课表/日程/选课/官方入口/活动查询 + ecosystem/ 生态工具）
├── services/        # 外部服务（CAS、jw、青春科大 young 客户端、活动推荐与偏好画像、LLM 熔断）
├── knowledge/       # 知识库文档（data/ 80 篇 md）与混合检索向量库
├── database/        # 应用/审核 SQLite Schema 与种子数据
├── frontend/        # React/Vite/TypeScript 四工作区 Web 前端
├── xiaowo_web/      # FastAPI、认证、SSE、证据、审核发布与 worker
├── deploy/          # SearXNG/Crawl4AI 安全 sidecar 模板
├── tests/web/       # Web API、权限、安全、发布与集成测试
├── ui/              # 迁移期保留的 Streamlit 回退界面
├── config/          # 官方链接与审核来源白名单
├── docs/            # 项目文档（会话交接摘要/接手日志/总纲方案/工具规格/团队材料）
├── scripts/         # 采集与验证脚本（评课重爬 SOP/青春科大快照/verify_* 回归系列）
├── PRODUCT.md       # Web 产品事实、用户、任务与不可回退契约
└── DESIGN.md        # Web 视觉令牌、响应式布局与组件规则
```

## 📄 文档

- [docs/会话交接摘要.md](docs/会话交接摘要.md) — **会话恢复唯一起点**（事实基准）
- [docs/接手日志.md](docs/接手日志.md) — 滚动状态与修复日志（⚡ 节为最新）
- [docs/总纲与工具接口方案.md](docs/总纲与工具接口方案.md) — 总体开发方案（Phase 3-6）
- [docs/tool-specs.md](docs/tool-specs.md) — Tool 详细规格说明
- [docs/小蜗_Web应用与联网RAG技术规格.md](docs/小蜗_Web应用与联网RAG技术规格.md) — 已确认的 Q1-Q80 Web/RAG 产品与技术契约
- [docs/Web部署与数据迁移.md](docs/Web部署与数据迁移.md) — 运行模式、sidecar、worker、加密数据包和 generation 回滚 SOP
- [docs/前端UI重构规格.md](docs/前端UI重构规格.md) — React 四工作区 UI 重构、可访问性与验收矩阵
- [DESIGN.md](DESIGN.md) — “冷色数字编目台”设计系统与组件约束
- [docs/dev-log/](docs/dev-log/) — 开发过程记录（finding 修复日志）
- [docs/team/](docs/team/) — 团队协作材料（备赛计划、协作指南、智能体配置）
- [docs/学习报告.md](docs/学习报告.md) — 项目学习基线

## ✅ 回归验证

| 命令 | 结果 |
|------|------|
| `python scripts/check_course_db.py` | 9/9（评课库完整性） |
| `python scripts/verify_tools.py` | 40/40（工具层断言） |
| `python scripts/test_fixes.py` | 50/50（历史修复与推荐语义回归） |
| `python scripts/verify_nodes.py` | 53/53（节点/路由/身份隔离校验） |
| `python scripts/verify_ecosystem.py` | 10/10（生态协议） |
| `python scripts/verify_links.py` | 12/12（官方链接/入口跳转） |
| `python scripts/verify_activities.py` | 8/8（活动查询；实时或快照降级） |
| `python scripts/verify_profile.py` | 11/11（偏好画像/均衡/快照回退） |
| `python scripts/verify_time_parser.py` | 17/17（自然语言时间与 GPA 表） |
| `python scripts/verify_security_ui.py` | 20/20（认证绑定、多用户方案隔离与 UI 安全） |
| `python scripts/e2e_program_identity.py` | 通过（桌面 1440×1000 / 移动 390×844；身份、来源、三标签、按钮与溢出） |
| `python -m pytest tests/web -q` | 100 passed（Web API、认证/权限、通知驱动 SSE、SSRF、结构化证据、审核队列与 generation） |
| `npm test` / `npm run build`（`frontend/`） | 11/11；生产构建成功，主入口 332.03 kB，Markdown 按需块 158.57 kB，无 chunk 警告 |
| `python scripts/e2e_web_workbench.py` | anonymous/demo/admin、Chat 3/2/1 与 Campus 4/3/2 响应式方块、1440/1024/390/320 浅深主题、三态分块审核 |
| `python scripts/verify_web_load.py` | 100 条真实消费中的 SSE、30 个并发回答、有界队列、`503 RUN_BUSY` 与 100 条完整终态 |
| `python scripts/qa_consistency.py` / `qa_new_docs.py` | 需 LLM；最近一次已确认基线 12/12 · 10/10 |
| 全模块 UI 问答实测 | 19 问 18 直接通过 + 1 断言误判（答案正确），见 docs/e2e_full_module_test.png |

### 已知限制

- 评课数据为 icourse.club 抓取快照（重爬 SOP：`scripts/refresh_course_db.py`）；样本量过少的课程/教师评分仅供参考
- 真实 CAS 登录部署需 service 白名单；本地开发用 `app_test.py` 离线路径
- 当前没有可信 HTTPS 域名，正式个人能力与 production 管理发布保持关闭；比赛只展示明确标识的合成 demo 数据
- 联网能力只有在服务器部署并核验 sidecar、结构化证据模型探针均通过后才启用；sidecar 不持有 CAS Cookie、校内凭证或用户私密上下文
- YOUNG_TOKEN 约 7 天有效期，失效后活动功能自动回退本地快照（来源如实标注）
- 教师对比样本量差异大（1~553 条）；推荐排序已做小样本收缩，原始均分和样本量仍会同时展示
- 课程推荐不会自动应用早八、晚课、星期或个人课表冲突过滤；确定候选后需单独运行课表冲突检查
- `scripts/test_fixes.py` 受限沙箱下可设 `XIAOWO_TEST_TMP`

## ⚖️ 说明

- 课程/课表/考试数据基于真实 catalog 抓取；成绩/课表/日程等个人数据登录后由 jw API 实时拉取，未登录时相关查询返回登录提示
- 敏感配置（`.env`）不进入版本库，密钥通过环境变量注入
