# 🐌 小蜗 — 科大校园智能助手

> 107 杯比赛项目 · 科大校园场景的 AI 智能体助手

小蜗是一个面向中国科学技术大学师生的校园智能助手，基于科大 LLM 平台（`api.llm.ustc.edu.cn`）构建，采用**统一 QA LangGraph** 智能体架构（意图分类 + 混合召回双通道 → LLM 自主决策循环 → 工具执行 → 统一回答），支持知识库问答与教务数据实时查询。

## ✨ 功能

| 模块 | 能力 |
|------|------|
| 📚 智能问答 | 基于 80 篇校园知识库文档（769 条向量分块）的 RAG 问答（办事、教务、生活、就业、科研与升学），回答可附官方来源网址；平台故障时自动降级 BM25 关键词检索 |
| 📊 课业助手 | 成绩查询、课表查询（节次精确到分钟）、GPA 计算、空教室查询；CAS 登录后经 jw API 拉取**真实教务数据** |
| 🔍 选课顾问 | 基于 icourse.club 真实评课数据（5667 个课程页 / 4.4 万条评论）与培养方案，按“必修 / 方案内选修 / 方向补充”推荐；本轮需求优先，课程范围和“只要/必须”是硬条件，其余偏好默认软排序；独立提供节次/周次级课表冲突检查与退补选压力评估 |
| 🧭 培养方案 | 登录后只使用当前用户 CAS/成绩档案中的专业、年级和个人方案；个人方案不可用时可按已验证身份显示醒目标注的“专业通用参考，不是个人培养方案”，不会继承登录前的匿名预览选择 |
| 📅 日程管理 | 日程记录与查询（自然语言时间解析："明天下午3点到4点"精确落点）、冲突检测、课表导入 |
| 🎪 活动推荐 | **青春科大（第二课堂）实时数据**：对话直接查报名中活动；每日弹窗四因子个性化推荐（紧迫度/课表空闲/个性化/热度），个性化=平台兴趣标签+行为学习+**德智体美劳模块学时均衡补短板**；token 失效自动回退本地快照 |
| 🔗 官方入口 | 强操作类诉求（选退课/评教/缴费等）给出**已核实**官方入口跳转 + 小蜗辅助（冲突检测/压力模拟）；侧边栏"校园导航"页收录 19 条官方工具与网站 |
| 🧩 生态工具 | 学生自制工具投稿接入（Spec + 函数三步，`eco:` 前缀强制署名，见 `tools/ecosystem/README.md`） |
| 🛡 可靠性 | LLM 平台断网/变慢三层降级：不假死、工具摘要直出、熔断不传染 |

所有降级/缓存数据均带有**来源标识**（实时数据 / 本地缓存 / 第三方工具），保证结果可追溯。

## 🛠 技术栈

- **前端/框架**: Streamlit（Web UI）
- **智能体**: LangGraph · 统一 QA 流程（embedding_parse → think 自主决策 ≤4 轮（规则 1-22）→ act → compose；确定性路由兜底）
- **知识库**: ChromaDB 混合检索（向量 + BM25，维度不匹配自动降级 BM25-only）
- **数据**: SQLite 双库——`database/xiaowo.db`（主库 + 日程 + 活动偏好）+ `data/course_data.db`（评课库 8 表）；青春科大个人快照 `scripts/data/young_personal/`
- **外部服务**: 科大统一身份认证（CAS）、教务系统（jw API）、**青春科大 young 平台（协议已逆向，12 个数据方法）**、科大 LLM 平台（降级路由）

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（复制 .env.example 为 .env 并填写）
#    LLM_API_KEY=你的科大 LLM 平台密钥
#    LLM_MODEL=deepseek-v4-flash
#    LLM_EMBEDDING_MODEL=qwen3-embedding

# 3. 构建评课库（选做；一键重爬 SOP 见 scripts/refresh_course_db.py --dry-run）
python scripts/crawl_icourse.py all   # 全量抓取 icourse.club 评课（断点续爬，可选）
python scripts/build_course_db.py     # 构建 data/course_data.db（8 表）

# 4. （可选）青春科大活动数据：.env 配置 YOUNG_TOKEN（登录 young.ustc.edu.cn
#    后 F12 → localStorage → pro__Access-Token-zsxc-base 的 value，约 7 天有效）
python scripts/crawl_young.py         # 生成个人快照（无 token 时活动功能自动降级）

# 5. 初始化检查（自动建库 + 知识库校验）
python init_check.py

# 6. 启动应用
streamlit run app.py
```

访问 `http://localhost:8501` 即可使用（未登录可体验知识库问答；课表/成绩/日程等个人数据需 CAS 登录后使用）。

> 注：应用内 CAS 登录跳转依赖 CAS service 白名单，本地开发使用 `http://localhost:8501`，部署后需配置实际域名。

## 📁 项目结构

```
├── agents/          # 智能体（qa/ 统一问答图 + tool_registry 28 内置工具注册表）
├── tools/           # 工具层（课程/成绩/课表/日程/选课/官方入口/活动查询 + ecosystem/ 生态工具）
├── services/        # 外部服务（CAS、jw、青春科大 young 客户端、活动推荐与偏好画像、LLM 熔断）
├── knowledge/       # 知识库文档（data/ 80 篇 md）与混合检索向量库
├── database/        # SQLite schema 与种子数据
├── ui/              # Streamlit 界面（对话/培养方案页/校园导航页/活动弹窗）
├── config/          # links.yaml 官方链接清单
├── docs/            # 项目文档（会话交接摘要/接手日志/总纲方案/工具规格/团队材料）
└── scripts/         # 采集与验证脚本（评课重爬 SOP/青春科大快照/verify_* 回归系列）
```

## 📄 文档

- [docs/会话交接摘要.md](docs/会话交接摘要.md) — **会话恢复唯一起点**（事实基准）
- [docs/接手日志.md](docs/接手日志.md) — 滚动状态与修复日志（⚡ 节为最新）
- [docs/总纲与工具接口方案.md](docs/总纲与工具接口方案.md) — 总体开发方案（Phase 3-6）
- [docs/tool-specs.md](docs/tool-specs.md) — Tool 详细规格说明
- [docs/dev-log/](docs/dev-log/) — 开发过程记录（finding 修复日志）
- [docs/team/](docs/team/) — 团队协作材料（备赛计划、协作指南、智能体配置）
- [docs/学习报告.md](docs/学习报告.md) — 项目学习基线

## ✅ 回归验证（2026-08-25 本地基线）

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
| `python scripts/qa_consistency.py` / `qa_new_docs.py` | 需 LLM；最近一次已确认基线 12/12 · 10/10 |
| 全模块 UI 问答实测 | 19 问 18 直接通过 + 1 断言误判（答案正确），见 docs/e2e_full_module_test.png |

### 已知限制

- 评课数据为 icourse.club 抓取快照（重爬 SOP：`scripts/refresh_course_db.py`）；样本量过少的课程/教师评分仅供参考
- 真实 CAS 登录部署需 service 白名单；本地开发用 `app_test.py` 离线路径
- YOUNG_TOKEN 约 7 天有效期，失效后活动功能自动回退本地快照（来源如实标注）
- 教师对比样本量差异大（1~553 条）；推荐排序已做小样本收缩，原始均分和样本量仍会同时展示
- 课程推荐不会自动应用早八、晚课、星期或个人课表冲突过滤；确定候选后需单独运行课表冲突检查
- `scripts/test_fixes.py` 受限沙箱下可设 `XIAOWO_TEST_TMP`

## ⚖️ 说明

- 课程/课表/考试数据基于真实 catalog 抓取；成绩/课表/日程等个人数据登录后由 jw API 实时拉取，未登录时相关查询返回登录提示
- 敏感配置（`.env`）不进入版本库，密钥通过环境变量注入
