# VM 接手文档（小蜗 → AI 代理完整上下文）

> 读者：部署在 114.214.241.119（东风云云主机）内的 DeepSeek Harness（DSH）AI 代理。
> 用途：AI 在主机中接管小蜗开发/运维前的**事实基线**。人用文档见 `docs/会话交接摘要.md`（唯一起点）与 `docs/接手日志.md`。
> 维护：随代码同步更新；快照基准 **2026-08-30**。

> ⚠️ **2026-09-01 更新（本机现状，优先于下文 Windows 描述）**：本主机已 Linux 化并完成部署——
> Ubuntu 24.04（8C/16G），DSH 运行于 bwrap 沙箱（/etc、/root 只读，无 systemd），工作区 `/root/Desktop/小蜗`；
> **部署已完成**（competition+demo @ `http://114.214.241.119:8850`，origin 单值校验），HEAD `980feb6`，数据资产（评课库/主库/审核库/chroma/young 快照）全部就位；
> 运维用 `deploy/server/{start_all,stop_all,status}.sh`；GitHub 走 Watt Toolkit 加速（**根证书 2026-08-31 轮换**，CA=`scripts/data/steamtools_ca_latest.pem`；Linux git 为 GnuTLS 后端、无 `http.sslBackend=openssl`）；
> 完整部署契约与调优记录见 `docs/部署规格与记录_2026-09-01.md`。下文 §6 Windows 表与 §8 清单仅作历史参考。

---

## 1. 项目是什么

**小蜗**：面向中国科学技术大学师生的校园智能助手（“107 杯”比赛项目）。基于科大 LLM 平台（`api.llm.ustc.edu.cn`）构建。

- 主 Web：React + Vite + TypeScript 四工作区前端，FastAPI `/api/v1` 后端与 SSE；Streamlit 只在迁移后一个版本内作为回退入口
- 智能体：LangGraph 统一 QA——`embedding_parse → think（≤4 轮，THINK 规则 1-22）→ act（工具执行）→ compose（合成回答）`，含确定性路由兜底与双层熔断（P3-2）
- 知识库：ChromaDB 混合检索（向量 + BM25 降级），80 篇权威文档 → 769 分块
- 数据：SQLite 双库（`database/xiaowo.db` 主库 + `data/course_data.db` 评课库）+ 青春科大个人快照
- 工具：28 内置（课业/选课/日程/方案/活动/官方入口…）+ 生态工具 `eco:` 前缀（Spec 驱动）
- 外部服务：教务 CAS（`jw.ustc.edu.cn`）、青春科大 young、科大 LLM 平台

能力面（功能模块）：智能问答 RAG / 课业助手（成绩·GPA·课表·空教室·考试）/ 选课顾问（均分推荐·同课多师·冲突检测·退补选压力）/ 培养方案（总览·学期规划·进度）/ 日程管理（自然语言时间·课表导入）/ 活动推荐（四因子：紧迫度·空闲·个性化·热度 + 均衡补短板）/ 官方入口链接（render_link + 校园导航页 19 条）/ 生态工具。

## 2. 当前状态快照（2026-08-30 实测）

| 项 | 值 |
|---|---|
| 本轮基线 HEAD | `bdb6d82`（main；本轮 UI、SSE 与文档收口仍为未提交工作区改动） |
| 分支/远端 | main ↔ https://github.com/AAAtrucklrx/107.git |
| 上个里程碑 | P5-1 `34e8858`、P5-2 `199449d` 已完成；`a88f6a8` P5-3 三联动已被当前工作区永久回滚 |
| 当前交付 | Web vNext + 联网提取、SSE 通知、审核三态、队列保留和 generation 发布 hardening；React 四工作区“冷色数字编目台”UI 已完成独立终审；实际最新提交以 `git log -1` 为准 |

### 2.1 已完成的推荐与身份边界（用户已确认 Q1-Q12）

| 文件 | 改动要点 |
|---|---|
| `agents/qa/graph.py` | `run_qa` 删除 `force_calls` 参数，`tool_calls` 恒 `[]` |
| `agents/qa/nodes.py` | 删除推荐侧 `schedule_constraints`；显式推荐动作优先于 embedding 首分类；“选修”名词本身不触发推荐；复合“推荐且不冲突”只推荐并写入未查冲突标记；独立冲突问句仍走 `check_course_conflict`；提取本轮明确兴趣与“只要/必须”硬偏好 |
| `tools/advisor_tools.py` | `recommend_courses` 分为“必修 / 方案内选修 / 方向补充”；课程范围与关键词是硬条件，兴趣/工作量/教师/目标学期默认软排序，仅“只要/必须”硬过滤；本轮显式需求覆盖旧画像；方向补充仅由本轮明确兴趣触发；个人方案优先，透明输出 `program_context` 与 `limitations`；工具签名永久移除 `schedule_constraints` |
| `app.py` | `process_query` 删除 force_calls；`pending_force_calls` 流向删除（方案页只留 pending_query 普通提问） |
| `ui/chat.py` | 完全移除 `pending_force_calls`；`_render_card` 展示推荐三组（必修/方案内选修/方向补充）和 `limitations` |
| `ui/program_page.py` | P5-3 三按钮（点评/推荐/查冲突/加日程弹窗）回退为单按钮 `_course_ask_buttons`（仅“问问小蜗”点评），删除 `_ask_recommend/_ask_conflict/_open_add_event/_add_event_dialog/pending_force_calls` |
| `services/cas_client.py` / `tools/program_tools.py` | 规范化嵌套学生档案的姓名、专业、年级；拒绝认证学号不一致的档案；测试方案树改为 CASClient 实例级；方案来源统一为 `personal/generic/unavailable` 并返回 `fallback_from_personal` |
| `ui/chat.py` / `ui/program_page.py` | 侧栏显示年级；登录/登出清理匿名方案选择和个人方案缓存；登录用户缺专业/年级时停止且不猜；页头显示身份、专业、年级、档案来源和方案来源；通用回退醒目标注“专业通用参考，不是个人培养方案” |
| `docs/选课顾问三联动_问题清单与修复对照.md` | 已删除（随回滚） |

### 2.2 验证现状（本机 2026-08-30 新鲜结果）

| 套件 | 结果 | 备注 |
|---|---|---|
| py_compile 全量 | ✅ 0 | |
| `verify_tools.py` | ✅ 40/40 | 含硬课程范围零命中不放宽 |
| `test_fixes.py` | ✅ 50/50 | 含软偏好回退、硬条件、方向补充和方案来源 |
| `verify_nodes.py` | ✅ 53/53 | 含复合推荐/独立冲突边界与工具签名 |
| `verify_profile.py` | ✅ 11/11 | |
| `verify_security_ui.py` | ✅ 20/20 | 含认证绑定、双用户方案树/缓存隔离、匿名状态清理 |
| `check_course_db / verify_ecosystem / verify_links / verify_activities / verify_time_parser` | ✅ 9/9 · 10/10 · 12/12 · 8/8 · 17/17 | 2026-08-30 已重跑 |
| `e2e_program_identity.py` | ✅ 通过 | 桌面 1440×1000 / 移动 390×844；身份、来源、三标签、按钮与横向溢出 |
| `pytest tests/web -q` | ✅ 100 passed | 认证/权限、通知驱动 SSE、终态竞态补读、SSRF、结构化证据、韧性、worker、审核与 generation 完整性 |
| `frontend: npm test / npm run build` | ✅ 11/11 / 成功 | Markdown/来源按需块 158.57 kB，主入口 332.03 kB，无 500 kB chunk 警告 |
| `e2e_web_workbench.py` | ✅ 通过 | anonymous、competition demo/admin、能力感知常见问题、精选校园入口、查询隔离、个人方案、审核治理与三态分块；1440 / 1024 / 390 / 320px 浅深主题 |
| `verify_web_load.py` | ✅ 通过 | 100 条真实消费中的 SSE、回答并发峰值 30、超限 503、100 条完整结束；事件 URL 按 `/api/v1` 相对契约解析 |
| 浏览器设计矩阵 | ✅ 通过 | 1440 / 1024 / 768 / 390 / 320px，浅深主题；无整页横向溢出、控制台错误、小于 12px 可见辅助文字或低于 40/44px 的控件 |
| 需 LLM：`qa_consistency` 12/12 · `qa_new_docs` 10/10 | 本轮未运行 | 脚本会向外部 LLM 发送学号/画像，未获明确授权；左侧为最近一次基线 |

> **2026-09-01 Linux 服务器实测**（代码已推进至 `980feb6`）：`pytest tests/web` **108/108**（100 旧 + 8 校园工具/学术新测试）；`verify_tools.py` 44/44 · `test_fixes.py` 49/49 · `verify_nodes.py` 57/57 · `check_course_db` 9/9；`verify_web_load` 通过（SSE=100 / 峰值并发 30 / 超限 503 / 完成 100）；init_check 通过（评课库 5667 门、向量 1000 条、检索命中）。

### 2.3 Web vNext 已落地边界

- 运行模式只有 `anonymous`、`demo`、`cas`。唯一 demo 身份为 `PB25111691 / 测试 / 计算机科学与技术 / 2025级`；demo 管理员只能操作 demo 审核库与 demo 索引。
- 当前没有可信 HTTPS 域名：比赛使用 `competition + demo`；无 HTTPS 的公开 production 只能 anonymous，个人区和管理区关闭；真实 CAS 只保留 Provider/API，等待域名和白名单。
- 本地资料不足、权威性不足或问题要求最新信息时才进入联网门控；搜索前永久禁止外发个人数据。确定结论要求一个审核官方一手来源或两个独立一致的可靠来源。
- SearXNG/Crawl4AI 只提供服务器 sidecar 模板，当前开发机未安装或启动。adapter、Redis/LLM 禁用契约或结构化提取能力探针未通过时，readiness 不放行联网。
- SSE 以 SQLite 为持久事实源，提交后通过进程内通知立即唤醒；1 秒读取作为跨进程兜底，事件窗口默认 1 小时且可配置。
- SSE 终态事件与 run 终态在同一事务提交；流观察到 terminal 状态时必须再补读一次，避免遗漏同事务写入的 `answer.completed` / `run.failed`。
- React 四工作区采用“冷色数字编目台”：≥1200px 为 216px 索引脊，761–1199px 为 72px 图标轨，≤760px 为顶部字标 + 底部导航；平板聊天历史走抽屉，审核采用索引页到全宽详情页。
- 校园服务使用 8 个配置驱动的高频启动方块与分类目录（4/3/2 列），活动保留独立时间列表；聊天空会话按公共/个人能力显示 6 个常见任务方块。培养方案模块使用连续账簿。桌面正文/辅助文字下限为 14/12px，移动正文与输入为 16px；桌面/移动控件下限为 40/44px。
- 产品与视觉事实分别固化在根目录 `PRODUCT.md`、`DESIGN.md`，UI 实施与验收契约见 `docs/前端UI重构规格.md`。
- 回答完成后异步入审核队列；每块必须明确批准或排除且至少批准一块，worker 领取后才从 `approved` 转 `pending_publish`。
- 发布任务关联具体条目并合并尚未领取的同 namespace 任务；失败不波及无关条目，激活前重验实时审核状态。Chroma 完整 generation 复用按模型与内容哈希校验的 embedding 缓存。
- done/dead 任务默认保留 7/90 天；orphan generation 从成为 orphan 起保留 7 天后清理，哈希墓碑和审核历史保留。回滚前验证 manifest、两套索引、元数据、数量和内容指纹。
- 完整技术契约见 `docs/小蜗_Web应用与联网RAG技术规格.md`，部署/迁移/回滚见 `docs/Web部署与数据迁移.md`。

## 3. 架构速览

- **QA 链**：`agents/qa/graph.py::run_qa`（入口）→ `agents/qa/nodes.py`（think/act/compose + THINK 规则 1-22 + `_direct_tool_route` 确定性路由 + `_enrich_*` 参数兜底 + `_build_tool_summary`/`_build_candidates_summary` 摘要）+ `intents.py`（15 类意图）
- **工具注册表**：`agents/tool_registry.py`（28 内置 + 生态合并；`_TOOL_LIST` 供 think）
- **服务层**：`services/young_client.py`（12 方法，AES-128-CBC 混淆，token 即密钥材料）、`activity_recommender.py`（四因子 + MMR）、`activity_profile.py`（冷启动/行为/均衡）、`cas_client.py`（jw CAS）、`llm_client.py`（熔断窗 600s 进程级）、`service_container.py`（单例 + session_ctx ContextVar 分桶）
- **降级设计**：三级（实时 → 本地缓存 → 提示）；LLM 熔断（连接级才开窗 600s）；RAG 维度不匹配 → BM25-only；嵌入器模块级缓存 + 5s 探测
- **防幻觉**：COMPOSE_PROMPT 规范（不编数据/不篡改工具结果/数值以工具为准/禁编 URL/来源标识）

## 4. 目录地图

```
agents/qa/       图/节点/意图（核心）
agents/tool_registry.py   注册表门面
frontend/        React/Vite/TypeScript 四工作区与组件测试
xiaowo_web/      FastAPI、认证、聊天/SSE、证据、审核、发布与 worker
tests/web/       Web API、安全、权限、发布与集成回归
deploy/          SearXNG/Crawl4AI sidecar 安全模板
tools/           课程/选课顾问/方案/日程/官方入口/活动/生态 ecosystem/
services/        young/活动推荐/画像/CAS/LLM 熔断/容器
knowledge/       data/ 80 篇 md + chroma_db（向量库，不入 git）
database/        schema.sql + xiaowo.db（不入 git）
ui/              chat/links_page/program_page/activity_dialog 等
config/          links.yaml（19 官方链接）；config.py（SEMESTER 等运行时配置）
scripts/         verify_* 回归系列、crawl_young、refresh_course_db(SOP)、dev_pipeline
docs/            会话交接摘要（人用）/接手日志/总纲方案/tool-specs
```

## 5. 数据资产与迁移（不入 git，新环境必备）

| 资产 | 位置 | 大小 | 说明 |
|---|---|---|---|
| 评课库 | `data/course_data.db` | 55.5MB | 5667 课程页/44418 评论/2982 教师/632 方案 |
| 主库 | `database/xiaowo.db` | 0.1MB | 测试账号 PB25111691：26 成绩/14 选课 + 日程/活动偏好表 |
| 向量库 | `knowledge/chroma_db/` | 39MB | 769 分块（80 篇 md） |
| young 快照 | `scripts/data/young_personal/young_snapshot.json` | 716KB | 报名中 19 + 已结束 1000 + 档案 + 标签 17 + 模块学时（德16/智17.5/体24/美17.5/劳40，总115） |
| 部署数据包 | `scripts/data/xiaowo_deploy_data.zip` | 40.9MB | 上 4 项的打包件（本机已生成） |
| 依赖锁 | `requirements.lock.txt` | 3.2KB | 163 个锁定版本（仓库根，未入库，单独迁移） |

知识库文档（80 篇 md）与 `config/links.yaml` 在 git 内。`config.py` 全部路径由 `PROJECT_ROOT` 派生；`SEMESTER = 2026-2027-1 / 2026-08-31`（校历核实，env `XIAOWO_SEMESTER_START` 可覆盖）。

### 5.1 云盘迁移通道（2026-09-02 定案：数据走云盘，不走 git）

**原则**：数据资产全部在 `.gitignore` 内（`data/`、`database/*.db` 及 WAL/shm、`scripts/data/`、`.models/`），git push/pull 永远带不上数据；**代码走 git，数据走云盘/文件直传**，两条通道不要混用。

**操作步骤（Windows 本机 → Linux 服务器）**：

1. 本机重新打包最新数据（注意：上表 `xiaowo_deploy_data.zip` 是旧快照，**不含** 2026-09-02 后的新数据）：
   ```powershell
   Compress-Archive -Path data/course_data.db, database/xiaowo.db, knowledge/chroma_db, scripts/data/young_personal -DestinationPath xiaowo_data_yyyyMMdd.zip
   ```
2. 上传云盘（百度网盘/OneDrive 等），服务器端用 `wget`/`curl` 或云盘客户端取回；取回后核对**文件大小/MD5** 与本机一致再继续。
3. **服务器先停服备份再覆盖**（服务器数据可能比本机新，方向性红线）：
   ```bash
   ./deploy/server/stop_all.sh
   cp -a data/course_data.db data/course_data.db.bak-$(date +%Y%m%d)   # 其余同理
   # 解压覆盖目标位置（保持属主与路径不变）
   ./deploy/server/start_all.sh
   ```
4. 迁移后必做：`.venv/bin/python init_check.py` + readiness 四项全绿 + 一条问答冒烟。

**方向性警告（2026-09-02 现状）**：服务器数据目前**领先于本机**——39 篇公众号知识已 active（review_db + 新 generation Chroma/BM25）、审核记录、`XIAOWO_EMBEDDING_MODE=local`（text2vec 768 维索引族）。**不要**用本机数据覆盖服务器的 `review.db` / `knowledge/chroma_db`，否则丢失已发布知识与审核历史；本机 → 服务器的迁移只补服务器**确实缺**的项（如服务器尚未建立的大体积评课库）。SQLite 的 `-wal`/`-shm` 文件必须随停服窗口一并处理（停服后拷贝或删除，运行中直拷会得到不一致快照）。

## 6. 环境事实

> **2026-09-01 起以本节「Linux 服务器」为准**；下方 Windows 开发机/云主机历史事实仅回溯用。

### Linux 服务器（当前主机 · 2026-09-01 实测）
| 项 | 值 |
|---|---|
| 系统/资源 | Ubuntu 24.04.4 LTS，8C/16G，磁盘 263G 可用 |
| DSH 运行形态 | bwrap 沙箱（PID1=bwrap）：**/etc、/root 只读**，无 systemd 总线；工作区 `/root/Desktop/小蜗` 为宿主机磁盘挂载（持久化） |
| Python | 3.12.3（无系统 pip/ensurepip）；仓库 venv `.venv`（get-pip 引导，pip 26.2.1） |
| Node/npm | v22.23.2 / 10.9.8；npm 缓存必须指工作区 `.npm-cache`（/root 只读） |
| 网络 | 校内 LLM/jw/young ✓、PyPI/npm/hf-mirror ✓；**github.com 仅经 Watt Toolkit 加速**（Steam++.Accelerator :443 + 根证书 8-31 轮换，CA=`scripts/data/steamtools_ca_latest.pem`） |
| 端口 | 80/443 宿主占用；Web 8000（公网 8850 转发，已建规则 `8850(虚拟IP)→8000(云主机)`）、Streamlit 8502（转发 8851 可选） |
| 部署 | competition+demo @ `http://114.214.241.119:8850`；管理 `deploy/server/{start_all,stop_all,status}.sh`；日志 `deploy/server/logs/` |

### Windows 历史事实（开发机 + 原 Windows 云主机，仅回溯）
- 开发机 Python 3.14：`C:\Users\Richelieu\AppData\Local\Programs\Python\Python314\python.exe`；PowerShell 无 `&&`（用 `;`）；沙箱 tempfile 受限用 `XIAOWO_TEST_TMP`；代理教训：FlClash/Steam++/iWan 叠加会劫持流量。
- 原 Windows VM 部署进行中清单（**已全部改为在 Linux 服务器完成，本清单作废**）：原计划 `competition + demo`、8850 端口转发、fastapi/worker/NSSM 等，均已按 `docs/部署规格与记录_2026-09-01.md` 落地（NSSM→nohup 脚本、venv 差异见上表）。
- [ ] 「出校」申请（公网访问，用户决定晚点提交；审批周期不可控）
- [ ] CAS 白名单 P3-1（登记 service URL；外部申请）
- [ ] **DSH AI 部署（本交接目的）**：`npx @deepseek-ai/dsh web`（Node ≥22.19，官方包）→ Web UI 3080；**官方 DeepSeek key 由用户自行配置**（api.deepseek.com）；形态 = Web UI + 端口转发（DSH 配置存 VM 用户目录 ~/.dsh）

## 7. 常用命令

```powershell
# 全量验证（见 AGENTS.md “必备命令”）
# 构建并启动主 Web（构建后的 SPA 由 FastAPI 同源托管）
cd frontend
npm ci
npm run build
cd ..
python -m uvicorn xiaowo_web.main:app --host 127.0.0.1 --port 8000
# 迁移期回退入口
python -m streamlit run app_test.py --server.port 8502 --server.headless true
# 评课库一键重爬 SOP（选课季前）
python scripts/refresh_course_db.py --dry-run      # 看命令链；--check-only 快速校验
# young 快照刷新（换 YOUNG_TOKEN 后）
python scripts/crawl_young.py
# 初始化/校验
python init_check.py
```

## 8. 已知问题与待办（按优先级）

1. **P3-1 CAS 白名单**（网络中心外部申请，审批不可控；正式登录前置）
2. **YOUNG_TOKEN 已失效**（2026-08-25 实测；F12 → localStorage `pro__Access-Token-zsxc-base`，换新后重跑 crawl_young；失效自动回退快照）
3. **itemized（个人已报名历史）接口未攻克**（headless 渲染不出，需用户真实浏览器抓包）
4. **平台越权接口已停用**（scExperience/list 不调用不存储；向校团委反馈待做）
5. **8-1 LLM 熔断窗 600s**（进程级全局，多用户 A 断网连累 B；建议缩至 60-300s——架构观察项，用户未拍板）
6. **出校申请 / 可信 HTTPS 域名**（真实 CAS、个人区和 production 管理区的硬前置）
7. **P6-1 流水线未完整纳入验证集**：`scripts/dev_pipeline/config.py` 仍只配置部分测试脚本。
8. **P6-3 剩余历史文档审定**：Web 核心运行文档已同步；`项目交接报告.md` 等历史总览仍需独立决定是否重写。

**2026-09-01 新增（服务器部署后发现）**：
10. **向量库维度降级**：现有 chroma 为 768 维（text2vec 时代构建），API embedding（qwen3-embedding）为 4096 维 → 按既有设计降级 BM25 检索（`knowledge/vector_store.py` 自动降级，检索仍可用；可选后续用 qwen3-embedding 重建索引，属数据资产变更需用户拍板）。
11. **LLM 客户端超时上限**：`config.py` LLM_CONFIG `timeout=30s`，>4000 字符长答案实测生成 ~32s 会撞超时（重试 1 次）；Web 层预算已调 60s/70s（.env）。用户已决定暂不改代码。
12. **Watt Toolkit 根证书轮换**：2026-08-31 换根，旧 `scripts/data/steamtools_ca.pem` 失效；git 通道必须以 `scripts/data/steamtools_ca_latest.pem` 为 CA，且本机 git 为 GnuTLS 后端（无 `http.sslBackend=openssl`）。

## 9. 密钥纪律

- `.env`：LLM_API_KEY（科大平台）、YOUNG_TOKEN、CAS_SERVICE_URL——**不进 git、不贴聊天/群**；本机与 VM 间复制。
- DSH：官方 DeepSeek API key 由用户配置，同样纪律。
- git push（沙箱专用 hack，见接手日志 ⚡ 节；VM 上正常凭据即可，勿照搬沙箱命令行）。

## 10. 首次工作建议步骤

1. 通读本文档 + `AGENTS.md`（DSH 自动加载）+（人用参考）`docs/会话交接摘要.md`。
2. `git status` + `git diff` 确认工作区；保留并理解未提交的推荐/身份/P5-3 回滚改动。
3. 跑全量验证（§2.2），记录实际结果，不沿用旧计数。
4. 推荐与培养方案语义已经用户确认；不得重新引入推荐侧排课过滤、`force_calls`、三按钮或跨用户身份兜底。
5. Web 改动先跑 `pytest tests/web -q`、前端测试/构建和 `scripts/e2e_web_workbench.py`；不得把 demo 提升为 production 发布者，也不得在 HTTP production 打开个人区或管理区。
