# VM 接手文档（小蜗 → AI 代理完整上下文）

> 读者：部署在 114.214.241.119（东风云 Windows VM）内的 DeepSeek Harness（DSH）AI 代理。
> 用途：AI 在 VM 中接管小蜗开发/运维前的**事实基线**。人用文档见 `docs/会话交接摘要.md`（唯一起点）与 `docs/接手日志.md`。
> 维护：随代码同步更新；快照基准 **2026-08-25**。

---

## 1. 项目是什么

**小蜗**：面向中国科学技术大学师生的校园智能助手（“107 杯”比赛项目）。基于科大 LLM 平台（`api.llm.ustc.edu.cn`）构建。

- 前端：Streamlit（`app.py` 正式版 / `app_test.py` 测试版，8501/8502 端口）
- 智能体：LangGraph 统一 QA——`embedding_parse → think（≤4 轮，THINK 规则 1-22）→ act（工具执行）→ compose（合成回答）`，含确定性路由兜底与双层熔断（P3-2）
- 知识库：ChromaDB 混合检索（向量 + BM25 降级），80 篇权威文档 → 769 分块
- 数据：SQLite 双库（`database/xiaowo.db` 主库 + `data/course_data.db` 评课库）+ 青春科大个人快照
- 工具：28 内置（课业/选课/日程/方案/活动/官方入口…）+ 生态工具 `eco:` 前缀（Spec 驱动）
- 外部服务：教务 CAS（`jw.ustc.edu.cn`）、青春科大 young、科大 LLM 平台

能力面（功能模块）：智能问答 RAG / 课业助手（成绩·GPA·课表·空教室·考试）/ 选课顾问（均分推荐·同课多师·冲突检测·退补选压力）/ 培养方案（总览·学期规划·进度）/ 日程管理（自然语言时间·课表导入）/ 活动推荐（四因子：紧迫度·空闲·个性化·热度 + 均衡补短板）/ 官方入口链接（render_link + 校园导航页 19 条）/ 生态工具。

## 2. 当前状态快照（2026-08-25 实测）

| 项 | 值 |
|---|---|
| HEAD | `28e53f7` fix: harden auth, scheduling, and Streamlit flows（2026-08-24） |
| 分支/远端 | main ↔ https://github.com/AAAtrucklrx/107.git |
| 上个里程碑 | P5-1 `34e8858`、P5-2 `199449d` 已完成；`a88f6a8` P5-3 三联动已被当前工作区永久回滚 |
| 工作区 | **有未提交改动**（推荐语义、认证档案/方案隔离、P5-3 回滚、测试与文档）；禁止擅自提交/还原 |

### 2.1 未提交改动明细（用户已确认 Q1-Q12）

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

### 2.2 验证现状（本机 2026-08-25 新鲜结果）

| 套件 | 结果 | 备注 |
|---|---|---|
| py_compile 全量 | ✅ 0 | |
| `verify_tools.py` | ✅ 40/40 | 含硬课程范围零命中不放宽 |
| `test_fixes.py` | ✅ 50/50 | 含软偏好回退、硬条件、方向补充和方案来源 |
| `verify_nodes.py` | ✅ 53/53 | 含复合推荐/独立冲突边界与工具签名 |
| `verify_profile.py` | ✅ 11/11 | |
| `verify_security_ui.py` | ✅ 20/20 | 含认证绑定、双用户方案树/缓存隔离、匿名状态清理 |
| `check_course_db / verify_ecosystem / verify_links / verify_activities / verify_time_parser` | ✅ 9/9 · 10/10 · 12/12 · 8/8 · 17/17 | 2026-08-25 已重跑 |
| `e2e_program_identity.py` | ✅ 通过 | 桌面 1440×1000 / 移动 390×844；身份、来源、三标签、按钮与横向溢出 |
| 需 LLM：`qa_consistency` 12/12 · `qa_new_docs` 10/10 | 本轮未运行 | 脚本会向外部 LLM 发送学号/画像，未获明确授权；左侧为最近一次基线 |

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

## 6. 环境事实

### 本机（开发机，沙箱受限）
- Python 3.14：`C:\Users\Richelieu\AppData\Local\Programs\Python\Python314\python.exe`
- PowerShell 无 `&&`；内联 `-c` 多行引号易炸 → 写脚本文件；沙箱 tempfile 受限用 `XIAOWO_TEST_TMP`
- 本机代理教训：FlClash/Steam++/iWan 叠加会劫持流量（“IP 未发送任何数据”元凶）；访问校内服务用 iWan，访问公网别开全局梯子

### VM（东风云 弹性云主机，Windows 11 Pro 8C16G）
| 项 | 值 |
|---|---|
| 内网 IP | 192.167.103.3 |
| 虚拟 IP（三层网络，默认不出校） | 114.214.241.119 |
| 控制台网关（noVNC，仅校园网/iWan） | 114.214.243.253:5000（4900 = VNC 后端） |
| RDP（已通） | `mstsc` → 114.214.241.119:23389（源端口 23389 → 云主机 3389） |
| 校内服务实测 | api.llm 200 ✅ · young 403(可达) ✅ · jw.ustc.edu.cn 302→/login ✅ · `jwxt.ustc.edu.cn` 无公网 DNS（**代码不用它**，真实域名 jw.ustc.edu.cn） |
| 平台端口转发 | 源端口禁用 22/53/514/123/7272/7274；规则：源端口(虚拟IP) → 云主机端口(VM)；RDP 已按此建好 |
| 密码 | 初始化时已自定义（用户自持）；A3 警示：未改默认密码做转发会收权限 |

**VM 部署进行中清单**（交接状态）：
- [x] RDP 远程登录打通
- [ ] 安装 Python 3.14（未装；`python` 命中 Store 假别名——用 `winget install Python.Python.3.14` 或官网安装器）
- [ ] 代码上机：`git clone https://github.com/AAAtrucklrx/107.git C:\xiaowo`（GitHub 可达性待测；不通则 RDP 拖 zip）
- [ ] 数据上机：拷贝 `xiaowo_deploy_data.zip` + `requirements.lock.txt`（RDP 驱动器重定向 `\\tsclient\F\小蜗\...`）→ `Expand-Archive` 到 C:\xiaowo
- [ ] `.env`：从本机复制（LLM_API_KEY/LLM_MODEL=deepseek-v4-flash/LLM_EMBEDDING_MODEL=qwen3-embedding/CAS_SERVICE_URL=http://114.214.241.119:8850/YOUNG_TOKEN），不进 git
- [ ] `python -m venv .venv` + `pip install -r requirements.lock.txt` + `python init_check.py`
- [ ] 启动 Streamlit（8501）+ NSSM 注册服务（开机自启/崩溃重启）
- [ ] 平台端口转发：源端口 8850 → 云主机 8501（**校内网址** http://114.214.241.119:8850）
- [ ] 「出校」申请（公网访问，用户决定晚点提交；审批周期不可控）
- [ ] CAS 白名单 P3-1（登记 service URL；外部申请）
- [ ] **DSH AI 部署（本交接目的）**：`npx @deepseek-ai/dsh web`（Node ≥22.19，官方包）→ Web UI 3080；**官方 DeepSeek key 由用户自行配置**（api.deepseek.com）；形态 = Web UI + 端口转发（DSH 配置存 VM 用户目录 ~/.dsh）

## 7. 常用命令

```powershell
# 全量验证（见 AGENTS.md “必备命令”）
# 启动演示/正式（VM 上无沙箱，直接）
python -m streamlit run app_test.py --server.port 8502 --server.headless true
python -m streamlit run app.py --server.port 8501 --server.headless true
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
6. **出校申请**（公网访问网址关键路径，用户安排晚点提交）
7. **P6-1 流水线未完整纳入验证集**：`scripts/dev_pipeline/config.py` 仍只配置部分测试脚本。
8. **P6-3 剩余文档审定**：核心运行文档已在本轮同步；`项目交接报告.md` 的历史总览仍需独立决定是否重写。

## 9. 密钥纪律

- `.env`：LLM_API_KEY（科大平台）、YOUNG_TOKEN、CAS_SERVICE_URL——**不进 git、不贴聊天/群**；本机与 VM 间复制。
- DSH：官方 DeepSeek API key 由用户配置，同样纪律。
- git push（沙箱专用 hack，见接手日志 ⚡ 节；VM 上正常凭据即可，勿照搬沙箱命令行）。

## 10. 首次工作建议步骤

1. 通读本文档 + `AGENTS.md`（DSH 自动加载）+（人用参考）`docs/会话交接摘要.md`。
2. `git status` + `git diff` 确认工作区；保留并理解未提交的推荐/身份/P5-3 回滚改动。
3. 跑全量验证（§2.2），记录实际结果，不沿用旧计数。
4. 推荐与培养方案语义已经用户确认；不得重新引入推荐侧排课过滤、`force_calls`、三按钮或跨用户身份兜底。后续优先级在 P6-1、VM 部署和外部申请之间另行选择。
