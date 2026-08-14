# 小蜗项目精读摘要（knowledge / database / utils / ui / agents 遗留 / dev_pipeline）

> 阅读范围：13 项全部读完，无"未读"。schema.sql 与 schema_course.sql 已核对（用户已读过，此处仅核对建表数并给观感）。

---

## 1. knowledge/vector_store.py（341 行）

**职责**：基于 ChromaDB 的 FAQ 混合检索存储，向量 + BM25 双路检索、RRF 融合，三层 embedding 降级。

**关键类/函数**：
- `FAQVectorStore`：主类。`__init__` 初始化 PersistentClient 并建 `faq_knowledge` 集合（cosine），持久化损坏时自动 `_nuke_chroma_db` 重建（L36–50）。
- `_init_embedding`：三级降级 api(OpenAI 兼容) → local(SentenceTransformer) → fallback(_KeywordEmbedder)（L68–100）。
- `add_documents`：写向量，id 追加 `_chunk{n}`（L102–119）。
- `search`：向量候选池 `top_k*3` 扩招 + BM25 候选 → `_rrf_merge`(k=60) 融合排序取 top_k；`found` 按 `THRESHOLD_MAP` 判阈值（L121–183）。
- `_rrf_merge`（L224–230）。
- `_tokenize_cjk`：中文单字+bigram、英文词保留（L233–257）。
- `_BuiltinBM25`：rank_bm25 缺失时的内置 BM25 兜底（L260–295）。
- `_nuke_chroma_db`：彻底清空持久化目录（L298–309）。
- `_APIEmbedder`：OpenAI 兼容 API embedding 适配器，统一 encode 接口（L312–322）。
- `_KeywordEmbedder`：384 维哈希向量化兜底（L325–341）。

**风险点（行号）**：
- 阈值双源——`THRESHOLD_MAP`（L19）与 `config.FAQ_SIMILARITY_THRESHOLD=0.6` 并存，search 实际用 THRESHOLD_MAP（L180），config 值仅兜底默认，易漂移。
- 每次 `FAQVectorStore()` 新建实例（search / intent_classifier 均如此），embedding 模型不跨实例缓存，有重复连接开销。
- 最终 score/found 只看向量分 `1-distance`（L140/166/180），BM25 只影响排序，纯关键词命中时 top_score 可能为 0。
- `_nuke_chroma_db` 无备份，清空不可逆。

---

## 2. knowledge/document_loader.py（184 行）

**职责**：加载 `knowledge/data/` 下 Markdown FAQ 文档，解析元数据并分块。

**关键函数**：
- `load_faq_documents`：遍历分类目录逐 md 解析，生成带 `chunk_index` 的文档列表（L14–63）。
- `_parse_faq_doc`：解析标题/子分类/关键词/来源/最后更新 + `is_official`（L66–121）。
- `_extract_section`：提取 `## 正文` / `## 注意事项` 小节（L124–137）。
- `_split_into_chunks`：按空行分段，>200 字段落触发句子级切分（L140–151）。
- `_split_long_text`：句子/列表行切分，SENTENCE_GROUP_LEN=70 合并（L154–175）。
- `_is_official`：含"非官方/同学经验/仅供参考/学长经验"任一 → False（L178–184）。

**风险点（行号）**：
- 元数据靠约定 md 结构（`## 分类` / `## 关键词` / `## 相关链接` 下一行），结构不符则字段为空（L80–99）。
- "最后更新"解析疑似 bug：`line.split("|")[-1]`（L106）对 `| 最后更新: 2026-07-01` 会取到"最后更新: …"而非日期（L103–107）。
- `_is_official` 纯黑名单，正向官方来源不校验（L178–184）；"仅供参考"一词可能误伤官方说明。

---

## 3. knowledge/intent_classifier.py（99 行）

**职责**：12 类意图分类，示例句向量相似度；embedding 失败降级 keyword 匹配。意图定义单一来源在 `agents/qa/intents.py`。

**关键类/函数**：
- `IntentClassifier`：`_get_embedder` 复用 `FAQVectorStore().embedding_model`（L26–30）；`_cached_example_vectors` 归一化缓存（L32–40）；`classify` 返回 `{"intent","top3","method"}`（L42–50）；`_classify_by_embedding`（余弦相似度，L52–68）；`_classify_by_keyword`（token 交集计数，L70–83）。
- `classify`：模块级单例入口（L89–94）。

**12 意图**：知识问答 / 查成绩 / 查课表 / 查空教室 / 查考试 / 课程搜索 / 选课推荐 / 教师点评 / 日程查询 / 日程管理 / 闲聊 / 敏感拒绝。

**风险点（行号）**：
- 嵌入分类无置信度阈值，top1 一律返回；低置信度修正依赖 nodes.py 的 `_MODULE_HINT_MIN_SCORE=0.5`。
- keyword 分数量纲（交集个数）与 embedding 分不同，调用方只看 method/intent 可接受。

---

## 4. database/db_manager.py（119 行）

**职责**：SQLite 操作封装——线程本地连接、dict 行、事务。

**关键 API**：
- `get_conn`：线程本地懒建连接，row_factory=sqlite3.Row，PRAGMA foreign_keys=ON（L37–45）。
- `init_schema`：读 schema.sql 并 executescript（L47–56）。
- `execute`：写操作，返回 lastrowid（L58–63）。
- `executemany`：批量写（L65–68）。
- `query`：读操作，返回 list[dict]（L70–75）。
- `query_one`：单行 dict|None（L77–79）。
- `run_script`：多语句脚本（seed）（L81–85）。
- `transaction`：上下文管理器，支持嵌套（外层才 commit/rollback）（L87–104）。
- `table_exists` / `close`（L106–119）。

> ⚠️ **任务所说 `query_all` 实际不存在**——代码里读接口名是 `query`（返回全部行），无 `query_all` 方法（已 grep 确认全仓库无 `def query_all`）。

**风险点（行号）**：
- 连接懒建但无自动回收（仅显式 close），Streamlit 缓存资源下常驻可接受。
- 主库 schema.sql 未开 WAL（仅 schema_course.sql 开了）。

---

## 5. database/seed_data.py（8 行）

**职责**：`SEED_SQL` 种子数据。**当前 `SEED_SQL = ""`（空）**。docstring 说明：课程检索已切到 `data/course_data.db`（2351 门，由 `scripts/build_course_db.py` 构建），本地 courses 表不再需要种子；课表/成绩登录后由 jw API 拉取缓存到 student_courses。

**规模**：0 条 SQL。属"保留接口、内容已清空"的演进痕迹——app.py L21 与 graph.py L76 仍导入并传入 `seed_sql=SEED_SQL`。

---

## 6. database/schema.sql 与 schema_course.sql 核对

**db_manager 覆盖情况**：`init_schema` 默认读 `config.SCHEMA_PATH = schema.sql`（主库 xiaowo.db），8 张表全部建：
courses / student_courses / student_grades / course_reviews / teacher_reviews / events / reminders / chat_history。覆盖无遗漏。

**schema_course.sql 实际建表数 = 8 张（不是 9）**：
courses / reviews / course_rates / course_terms / teachers / course_teachers / programs / program_courses。

**course_data.db 实际表清单（sqlite_master 实测）**：

| 表 | 行数 |
|---|---|
| course_rates | 2282 |
| course_teachers | 5371 |
| course_terms | 8793 |
| courses | 2351 |
| program_courses | 45168 |
| programs | 632 |
| reviews | 44403 |
| teachers | 2981 |
| sqlite_sequence | 6 ← SQLite 内部表（AUTOINCREMENT 自动生成，非业务表） |

**结论**：交接报告称"8 张"**正确**；"9 张"的来源是把 sqlite_master 里的内部表 `sqlite_sequence` 也计入了。schema_course.sql 本身是 8 个 `CREATE TABLE`。

**观感**：schema_course.sql 注释详尽、索引齐备，明确"主评分=星级真实均分（不归一化）、维度均分为文字映射分仅参考"的数据口径，质量好。

---

## 7. utils/（5 个文件）

- **logger.py**：`get_logger` 命名 logger，handler 只挂 "xiaowo" 根 logger 防重复（36 行）。风险：若子 logger 先挂 handler 而根无 handler 会无输出（L24 判断的边角）。
- **llm_client.py**：`create_llm` 返回 langchain `ChatOpenAI`，读 LLM_CONFIG（25 行）。无重试/结构化封装；api_key 默认占位 "your-api-key-here"。
- **time_parser.py**：`parse_natural_time` 解析 ISO 日期/今天明天/下周X/周X/时段/节次（124 行）。风险：docstring 声称支持"第5周"但代码**未实现**；"下周X" 双重 +7 可读性差（L68–75）。
- **gpa_calculator.py**：科大 4.3 制，`score_to_grade_point` 百分制→绩点对照表 + `calculate_gpa`（52 行）。`calculate_gpa` 直接用 grade_point 而非重算 score，依赖调用方传对。
- **course_periods.py**：节次 1–13 → 精确起止时间（来源官网含 URL 注释），`parse_periods`/`periods_to_range`（64 行）。风险：不连续节次取最早开始/最晚结束，中间空档也算进范围（L58–59）。

---

## 8. ui/chat.py（307 行）

**职责**：Streamlit UI——页面初始化、CSS、侧边栏、CAS 登录、聊天区。

**关键函数**：
- `init_page`：set_page_config（L13–20）。
- `apply_custom_css`：注入 CSS（L23–65）。
- `render_sidebar`：登录面板 + 模块 radio + 清空对话 + 最近对话 + 版本，返回模块名（L68–113）。
- `_render_login_panel`：已登录显示用户信息/过期>3600s/登出；未登录渲染 CAS 跳转链接（L116–153）。
- `_do_login`：账号密码登录（L156–179）。
- `_logout`：清 user、ServiceContainer.reset、清 4 个 agent 缓存 key、清 query_params（L182–196）。
- `check_cas_callback`：读 ticket → login_with_ticket → 写 user → 清 ticket（L199–241）。
- `render_chat_area`：渲染历史 + chat_input，返回 prompt（L244–268）。
- `add_assistant_message`：追加 assistant 消息（L271–278）。
- `show_thinking_indicator` / `show_tool_call`：辅助（L281–307）。

**session_state 键**：`messages`（对话 list[dict]）、`user`（id/name/major/grade/logged_in_at）、`_cas_ticket_processed`（CAS 防重）、`_young_dialog_shown`（活动弹窗标记，见 activity_dialog）、`faq/course/advisor/schedule`（旧 agent 缓存，仅 `_logout` 清除用）、`profile_major/profile_grade/program_year_select`（program_page 用）。

**登录流程**：未登录 → 点 CAS 链接跳 id.ustc.edu.cn → 回调带 `ticket` → app.main 调 `check_cas_callback` → `login_with_ticket` → 写 `user` → 清 ticket → rerun。

**风险点（行号）**：
- `_do_login`（L156）当前**未被任何地方调用**（已 grep 确认，仅定义），是死代码——未登录分支只渲染 CAS 链接，无账号密码入口。
- 过期阈值 3600 硬编码（L123）。
- `_logout` 清除的 `faq/course/advisor/schedule` 是新架构已不使用的旧 key（L189–190）。
- CSS `:has(+ .user)` 依赖 Streamlit DOM 结构，脆弱（L46）。

---

## 9. ui/program_page.py（322 行）

**职责**：培养方案三合一页面（我的方案 / 学期规划 / 进度概览）。

**关键函数**：
- `render_program_page`：主入口，取用户上下文 → 未登录降级画像/选择器 → 拉个人方案树 → `get_my_program` → 顶部进度条 → 三 tab（L241–311）。
- `_get_user_context`（L28–40）。
- `_load_taken`：读 student_grades 已修（L43–57）。
- `_pull_personal_tree`：CAS 拉个人方案树，失败降级全量库（L60–70）。
- `_program_options`：全量库读专业/年级（L80–95）。
- `_render_my_program`：方案名+模块构成+按模块课程表（L107–149）。
- `_render_semester_plan`：第 N 学年课程表（L152–185）。
- `_render_progress`：进度条+必修缺口+模块进度（L188–236）。
- `_estimate_years`：按 term 前缀估算学年数（L314–322）。

**风险点（行号）**：
- 依赖 `tools.program_tools` 三工具（get_my_program/plan_semester/get_program_progress）`.invoke()`（L155/268/285）。
- `_program_options` 直接 sqlite3 连 COURSE_DB（L84–93），绕过 DatabaseManager，风格不统一。
- 顶部进度条与 `_render_progress` 内部进度条略有重复（L293 vs L206）。

---

## 10. ui/activity_dialog.py（91 行）

**职责**：校团委活动推荐弹窗，每天最多一次。

**关键函数**：
- `DIALOG_LOG_PATH = data/young_dialog_log.json`（L16）。
- `_load_log` / `_save_log`：JSON 持久化 `{student_id: 日期}`（L19–34）。
- `shown_today`：会话内标记 + 文件双保险（L37–41）。
- `mark_shown`：打开前标记（L44–49）。
- `show_activity_dialog`：`@st.dialog` 模态弹窗，Top 推荐 + 全部活动（L61–91）。
- `_fmt_activity_time`：时间展示（L52–58）。

**日志机制**：按"用户+日期"写 `young_dialog_log.json`；app.py `maybe_show_activity_recommendation`（L70–94）先 `shown_today` → 拉 YoungService → `recommend` → `mark_shown` → 弹窗。

**风险点（行号）**：
- JSON 全量读写无锁，多会话并发低风险竞态。
- `_fmt_activity_time` 里 `replace(' ', ' ')` 是无效替换（L55，疑似笔误）。

---

## 11. agents/ 遗留文件死代码判定

**证据链**：app.py 唯一入口 `from agents.qa.graph import run_qa`（app.py:57）；`agents/qa/*` 只 import `executor._build_tool_registry` + intents + intent_classifier + knowledge。全仓库 grep `route_query|create_plan|build_agent|create_*_agent|Executor(` 均无新链路调用点。

| 文件 | 是否被新链路引用 | 引用的具体位置/证据 |
|---|---|---|
| router.py | ❌ 死代码 | `route_query` 仅本文件定义；app.py 走 run_qa，无任何 import |
| planner.py | ⚠️ 仅被传递导入，功能死代码 | executor.py:11 `from agents.planner import Plan, PlanStep`；Plan/PlanStep 只被死代码 Executor 类用；`create_plan/validate_plan` 无调用 |
| executor.py | ✅ 部分复用（仅 `_build_tool_registry`） | nodes.py:16 import，act() L650 调用；其余 Executor/execute/_execute_step/_synthesize 等全为死代码 |
| factory.py | ❌ 死代码 | `build_agent/invoke_agent` 仅被 4 个死子模块 import（各文件 L7） |
| context.py | ⚠️ 仅被传递导入，功能死代码 | executor.py:10 `from agents.context import Context`；Context 只被死代码 Executor 用 |
| advisor_agent.py | ❌ 死代码 | `create_advisor_agent` 仅本文件定义 |
| course_agent.py | ❌ 死代码 | `create_course_agent` 仅本文件定义 |
| faq_agent.py | ❌ 死代码 | `create_faq_agent` 仅本文件定义 |
| schedule_agent.py | ❌ 死代码 | `create_schedule_agent` 仅本文件定义 |

**注意**：planner.py、context.py 因 executor.py 的模块级 import 会被"传递导入"（import 时执行其模块体），但功能上不被新链路使用——属"可导入但功能死代码"。新链路复用 executor 的 `_build_tool_registry`（其内部 import 了 tools/faq_tools、course_tools、advisor_tools、program_tools、schedule_tools 这 5 个新工具模块，均已确认存在）。

---

## 12. scripts/dev_pipeline/ 目录

**职责**：独立"开发流水线"——用 LangGraph 编排"计划→执行→测试→报告→决策循环"，把开发任务交给 Claude/Codex/Qoder CLI 多轮迭代直到验收通过。与主应用 app.py 无耦合。

**文件**：
- `__main__.py`：`python -m scripts.dev_pipeline` 入口（5 行）。
- `cli.py`：argparse，`run`（task/--executor/--rounds/--json）与 `status` 子命令（94 行）。
- `graph.py`：StateGraph 编排 collect_context→plan→execute→test→decide→(execute|report)→canvas→END（84 行）。
- `nodes.py`：各节点实现——git 状态快照/文件哈希追踪变更、subprocess 调 CLI、`compileall` 语法检查（293 行）。
- `state.py`：PipelineState TypedDict（40 行）。
- `config.py`：路径/LLM(deepseek-v4-flash)/执行器 CLI 路径/测试脚本清单（45 行）。
- `llm.py`：`get_llm/ask` 封装（28 行）。
- `report_canvas.py`：渲染 Qoder `.canvas.tsx` 报告（138 行）。

**风险点（行号）**：
- execute_node 直接 subprocess 调外部 CLI 且带 acceptEdits 权限（nodes.py L130/L146），安全约束仅靠 prompt 文本，非强隔离。
- graph.py 的 `_route_after_execute`/`_route_after_decide` 是未使用的死函数（L18–25，实际用内嵌 decide_route）。
- config.py CODEX_BIN 硬编码含用户路径 hash（L23）。

---

## 13. rebuild_kb.py 与两个备份目录

**rebuild_kb.py**：全量重建 FAQ 向量索引。`py rebuild_kb.py`（预览，不删数据）/ `--yes`（执行）。流程：`_nuke_chroma_db` 清空 → FAQVectorStore → `load_faq_documents` → `add_documents` → 分块统计+元数据完整性检查（缺失字段/超 600 字块）→ 6 条检索测试。设 `HF_ENDPOINT` 镜像。

**两个备份目录**（均为 ChromaDB PersistentClient 持久化目录，结构相同 = `chroma.sqlite3` + 一个 HNSW segment 目录含 4 个 .bin）：

- `knowledge_backup_20260812_115213/`：集合 faq_knowledge，**50 条向量**，cosine；segment UUID f0570b32…（sqlite 1.38MB；segment .bin 时间戳 2026/8/9）。
- `knowledge_backup_20260812_115234/`：集合 faq_knowledge，**98 条向量**，cosine；segment UUID 33c87790…（sqlite 2.2MB；.bin 时间戳 2026/8/12）。

**差异**：两次独立构建，向量数 50 vs 98，collection UUID 不同。二者 segment 的 `data_level0.bin` 字节数相同（1,652,400 B），但我只比对了文件大小与 sqlite 元数据，**未做逐字节 diff**，这点如需可自行核实。

---

## 总体观感（供报告）

项目已完成从"router/planner/executor + 4 子 Agent"的 v2 架构向"统一 QA LangGraph（embedding_parse→think→act→compose）"v3.0 架构的迁移，但 `agents/` 下 9 个遗留文件未清理（仅 executor._build_tool_registry 被复用）；部分文档与代码脱节（seed_data 已空、`_do_login` 死代码、db_manager 无 `query_all`、time_parser 声称支持第N周但未实现），学习报告可据此说明"演进痕迹 vs 现状"的差异。
