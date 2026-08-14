# 小蜗（科大校园智能助手）核心工具与服务模块精读摘要

> 读取范围：`tools/advisor_tools.py`、`tools/course_tools.py`、`tools/program_tools.py`、`tools/schedule_tools.py`、`tools/faq_tools.py`、`tools/api_client.py`、`services/cas_client.py`、`services/young_client.py`、`services/activity_recommender.py`。全部逐行读完，无未读段落。

---

## 1. `tools/advisor_tools.py`（选课顾问工具）

**一句话职责**：基于本地评课库（icourse.club 真实评课，`data/course_data.db`）做课程推荐、对比与教师分析，画像只做"软过滤 + 理由"，不参与排序。

**关键函数清单**（27 内部 + 4 `@tool` = 31 个）：

| 函数(签名) | 返回 | 数据源 | 来源标识 | 降级 |
|---|---|---|---|---|
| `get_profile()/update_profile(**kw)/reset_profile()` L90/94/98 | 画像 dict / 原地更新 / 重置 | 内存模块单例 `_current_profile` L87 | 无 | 无 |
| `_cdb()` L43 | 新 sqlite 连接（`timeout=10`） | 本地库 | — | — |
| `_norm_course_name(name)` L50 | 归一化课程名（去括号/空格/引号，ASCII 大写） | 纯函数 | — | — |
| `_match_courses(conn,name)` L72 | 课程模糊匹配（含均分/样本量），按样本量降序 | 本地库 | 无 | 无 |
| `_resolve_program(conn,major,grade)` L315 | `(program_id, program_name)` 或 `(None,None)` | 本地库 | 无 | name 命中优先，无则回退 college LIKE L326-334 |
| `_pool_rows(conn,keywords,limit=200)` L414 | 全量评分候选池（均分降序） | 本地库 | 无 | 无 |
| `_recommend_flat(...)` L477 | 纯评分推荐 dict（无方案命中） | 本地库 | `keyword_fallback` bool | 关键字无命中回退全量池 L482-484 |
| `_recommend_grouped(...)` L501 | 必修组+选修组两段式 dict | 本地库 | `keyword_fallback:False` | 必修不足由选修补足 L553 |
| `_build_item(conn,row,profile)` L433 | 单课完整条目（dims/teachers/reviews/program_hint/reasons） | 本地库（多表 N+1 查询） | 无 | 无 |
| `_generate_reason(course,profile)` L264 | 兴趣匹配 + 画像提示理由列表 | 内存 | — | 样本<10 追加"仅供参考" L285 |
| `@tool collect_preferences()` L589 | 偏好收集状态 dict | 内存画像 | `status` | — |
| `@tool recommend_courses(profile/major/grade/interests/preference_type/preference/keywords/max_results/taken_courses/current_year_index/current_term)` L610 | 推荐结果 dict（`recommendations/groups/progress/total_candidates/filtered_count/profile_note/keyword_fallback`） | 本地库 | 无 source；`keyword_fallback` | DB 不可用返回 `error` dict L676-678；方案未命中→纯评分 L702 |
| `@tool compare_courses(course_a,course_b)` L714 | 两课对比（评分/难度/给分/收获 winner + suggestion） | 本地库 | 无 | 未找到返回 `error` L750/754 |
| `@tool analyze_teacher(teacher_name,course)` L781 | 教师模式 `{teacher,courses,avg_rating,review_count,reviews_sample}` 或课程模式 `{course,teachers,...}` | 本地库 | 无 | 多近似名返回 `ambiguity` L818 |

**关键逻辑**：排序严格按真实星级均分降序、平手看样本量（`_pool_rows` L428；必修组 `_term_urgency` L359 分档：已过期应修置顶→当前学年下学期→未来）；画像软过滤只生成 `reasons`，不进排序权重（L264-287）。

**风险点**：
- **线程安全/多用户污染**：`_current_profile` 是模块级全局单例（L87），Streamlit 多用户并发下会串画像（跨用户污染）。
- **N+1 查询**：`_build_item` 对每门课串行调用 `_dims_info/_teacher_cells/_top_reviews/_recent_terms/_program_hint`（L433-466），池 200 条时查询量巨大，无批量化。
- **连接泄漏**：`recommend_courses`/`compare_courses` 用 `conn.close()` 手动关闭而非 `with`/`finally`（L708、L749），中途抛异常会泄漏连接。
- **`_norm_course_name` 三处实现不一致**：本文件 L50 只替换空格/全角空格，未用 `ch.isspace()` 过滤所有空白（`program_tools.py` L35 则更彻底），DRY 违背且易出现匹配分歧。
- **`_resolve_program` 双实现不一致**：本文件 L315 含 `_prog_priority`（普通/英才/辅修三级优先级 L302），`program_tools.py` L67 版本无此优先级，两处注释自称"一致"但实际不同。
- **硬编码阈值**：`_term_urgency`/`_infer_current_year_index` 硬编码 8/9 月分界与"秋/春"判定（L374-399）。
- **SQL 注入**：均为参数化（`?` 占位），`_pool_rows`/`_program_hint` 动态拼 `IN (...)` 也是参数化（L243、L419），未发现注入点。

**统计**：883 行，31 个函数。

---

## 2. `tools/course_tools.py`（课业助手工具）

**一句话职责**：课表/成绩/考试/课程搜索/选课/培养方案查询，优先实时拉取 jw 内部 API，失败回退 SQLite 缓存或"锁定/模拟"降级。

**关键函数清单**（14 内部 + 10 `@tool` = 24 个）：

| 函数(签名) | 返回 | 数据源 | 来源标识 | 降级 |
|---|---|---|---|---|
| `_db()/_catalog()/_cas()` L25/30/35 | DB / CatalogAPI / CASClient(或 None) | 容器单例 | — | `_cas()` 未登录返回 None L37 |
| `_is_locked(student_id)` L45 | bool（**忽略入参**，只看 `_cas() is None`） | 登录态 | — | — |
| `_fetch_real_schedule(cas,semester_id)` L121 | 标准课表 list 或 None | jw 实时 | — | 失败返回 None |
| `_sync_courses_to_db(sid,courses,semester)` L192 | 先删后插事务同步 | 写 SQLite | — | 事务回滚 |
| `_fetch_real_grades(cas)` L208 | 成绩 list 或 None（学期→成绩两级） | jw 实时 | — | 失败返回 None |
| `_sync_grades_to_db(sid,grades)` L282 | 先删后插事务同步 | 写 SQLite | — | 事务回滚 |
| `@tool query_schedule(student_id,week,day)` L326 | `{student_id,courses,count,source}` | 实时→SQLite | `real/fallback/locked` | 实时异常→fallback L370；未登录→locked L374 |
| `@tool query_daily_schedule(date,student_id)` L393 | 某日课程（精确到分钟） | 实时→SQLite | `real/fallback/locked` | 同上 L431-437 |
| `@tool find_empty_room(building,time_desc)` L471 | 空教室 list | 实时 timetable→模拟 | `real/fallback` | 实时异常→模拟数据 L558 |
| `@tool query_grade(student_id,course_name,semester)` L591 | 成绩 | 实时→SQLite | `real/fallback/locked` | 同上 |
| `@tool calc_gpa(student_id,semester)` L636 | GPA（4.3 制）+ 学分明细 | 实时→SQLite | `real/fallback/locked` | 同上 |
| `@tool query_exam(student_id,course_name)` L697 | 考试安排（专业课+通修课合并） | 实时→模拟 | `real/fallback` | 实时异常→模拟日期 L794 |
| `@tool search_courses(keyword,limit)` L816 | 课程搜索 | catalog→本地库+评课库合并 | `real/fallback` | 本地种子少，合并评课库 L863-878 |
| `@tool get_semester_list()` L886 | 学期列表（标当前学期） | catalog→硬编码 | `real/fallback` | 硬编码 2025-2026 L927 |
| `@tool query_course_selection(student_id,semester)` L937 | 选课结果 | jw→SQLite | `real/fallback/locked` | 同上 |
| `@tool query_program(student_id,module_id)` L1002 | 培养方案模块 | jw→硬编码 | `real/fallback/locked` | 硬编码通用模块 L1049 |

**`source` 标识语义**：`real`（实时成功）、`fallback`（本地缓存/模拟）、`locked`（未登录，附 `_LOGIN_MSG` L42）。

**风险点**：
- **隐私/越权**：`_is_locked` 完全忽略 `student_id` 参数（L45-47）；fallback 路径用传入的 `student_id` 直接查 SQLite（L378、L631），登录用户可传他人学号读取他人缓存数据，且实时路径用会话本人数据却同步到传入的 `sid` 下（L361），数据归属错乱。
- **`week` 参数无效**：`query_schedule` 声明并文档化 `week`，但真实路径与 fallback 均未使用（L327、L334-366），属死参数。
- **模拟/伪造数据**：`find_empty_room` fallback 生成 `i%3` 规则的空教室与假容量（L572-579）；`query_exam` fallback 用"今天+14+i*2 天"伪造考试日期（L801-809），标注了"模拟"但仍有误导风险。
- **成绩解析丢数**：`_fetch_real_grades` 用 `int(score)`（L255），等级制/非数字成绩（优秀/通过）会被静默丢弃（L256-257）。
- **硬编码过期数据**：`get_semester_list`/`query_program` fallback 硬编码学期与模块（L927-930、L1049-1054），时间推移后失真。
- **裸异常吞没**：`_get_current_semester_id` `except: pass`（L304-305）；多处 `except Exception` 仅记日志。
- 正则解析 `scheduleGroupStr`（`_parse_schedule_group_str` L79）依赖 jw 字符串格式，脆弱。

**统计**：1058 行，24 个函数。

---

## 3. `tools/program_tools.py`（培养方案工具）

**一句话职责**：方案定位、我的方案、进度概览、学期规划，支持登录后 `personal_tree`（jw 个人方案树）优先、否则全量库。

**关键函数清单**（13 内部 + 3 `@tool` = 16 个）：

| 函数(签名) | 返回 | 数据源 | 降级 |
|---|---|---|---|
| `_resolve_program(conn,major,grade)` L67 | 方案 dict（`{id,name,college,grade}`）或 None | 本地库 | name→college 回退 L81-85 |
| `_parse_tree(tree)` L153 / `_walk_tree` L163 | 个人方案树→统一 courses list | 入参 | 兼容 dict/list、`children/subModules` 双字段名 |
| `_resolve_courses(major,grade,personal_tree)` L211 | `(program_meta, courses)` | 个人树优先→全量库 | 解析为空回退全量库 L219-222 |
| `@tool get_my_program(major,grade,personal_tree)` L239 | `{program_id,name,college,grade,personal,totalCredits,modules,courses}` | 同上 | — |
| `@tool get_program_progress(major,grade,taken_courses,taken_credits,personal_tree)` L293 | `{required_total,required_taken,required_remaining,credits_taken,credits_required,percent,modules_progress}` | 同上 | 无已修传入→按方案 credit 估算 L336-338 |
| `@tool plan_semester(major,grade,year_index,personal_tree)` L380 | `{year_index,terms,total_credits}`（term 无前缀归"未标注"） | 同上 | — |

**关键逻辑**：`_parse_grade_key`（L61）把"2024级"转整数；`_resolve_program` 排序键 `(同年级, -年级数字)`——同年级→最近低年级→最新（L90-99）；个人树解析兼容实测结构（`type.nameZh`/`planCourses[].course`/`readableTerms`/`compulsory`，L166-192）。

**风险点**：
- **与 advisor_tools 的 `_resolve_program` 逻辑不一致**：本文件无 `_prog_priority`（英才班/辅修优先级），排序键也不同（本文件 `(bucket,-g)`，advisor 是 `(bucket,_prog_priority,-g)`），两处注释均称"一致"，实际不同，同一专业可能定位到不同方案。
- **进度学分口径**：`percent` 用学分比（L342），`required_taken` 用门数（L361-362），两种口径并存易混淆；`credits_taken` 缺省用方案行 credit 估算，与真实修读学分可能不符。
- **已修匹配靠课程名**：`_norm_taken_set`（L50）只按名称匹配，同名不同代码课程会被误判为已修。

**统计**：427 行，16 个函数。

---

## 4. `tools/schedule_tools.py`（日程管理工具）

**一句话职责**：日程增查、冲突检测、周视图、从课表导入重复日程（全部落 SQLite `events` 表）。

**关键函数清单**（3 内部 + 5 `@tool` = 8 个；任务描述称"7 个"，实际为 8 个）：

| 函数(签名) | 返回 | 数据源 | 来源标识 | 降级 |
|---|---|---|---|---|
| `_check_conflicts(student_id,start_time,end_time)` L22 | 冲突事件 list | SQLite | — | — |
| `_query_day_events(student_id,date_str)` L41 | 单日事件（含 recurring） | SQLite | — | — |
| `@tool add_event(student_id,title,start_time,end_time,location,description)` L65 | `{success,event_id,conflicts,has_conflict}` | SQLite 写 | — | 校验失败返回 `success:False` |
| `@tool get_day_view(student_id,date_str)` L116 | `{date,day_of_week,events,count}` | SQLite | 无 | — |
| `@tool get_week_view(student_id,start_date)` L142 | `{week_start,week_end,daily,total_events,busiest_day,free_days}` | SQLite | 无 | — |
| `@tool check_conflict(student_id,start_time,end_time)` L190 | `{has_conflict,conflicts}` | SQLite | 无 | — |
| `@tool import_schedule(student_id)` L210 | `{imported_count,courses,note}` | 复用 course_tools.query_schedule + 写 SQLite | 无 | 已存在去重 L253-258 |

> 说明：任务描述"全部 7 个函数"与实际不符——实际为 5 个 `@tool` + 3 个内部函数 = 8 个（`_db`、`_check_conflicts`、`_query_day_events`、`add_event`、`get_day_view`、`get_week_view`、`check_conflict`、`import_schedule`）。

**风险点**：
- **硬编码开学日期**：`base_date = date(2026,2,23)`（L226）与固定节次时间表 `sections_map`（L228-233），换学期即失效。
- **解析脆弱**：`import_schedule` 用 `part[:2]` 取星期、`part[2:]` 取节次（L246-247），对 `time` 字符串格式强依赖；且 `sections_map.get(section, 默认 08:00)` 会把未知节次静默归到第一节课。
- **无来源标识**：本模块返回无 `source` 字段，与 course_tools 的 `source` 约定不一致。
- **未登录越权**：`add_event`/`get_day_view` 等直接以 `student_id` 读写 events 表，无登录校验，与 course_tools 的 locked 机制不一致。

**统计**：278 行，8 个函数。

---

## 5. `tools/faq_tools.py` + `tools/api_client.py`

### 5.1 `tools/faq_tools.py`（50 行，3 个函数）

**一句话职责**：FAQ 向量库检索与分类查询的薄封装。

| 函数 | 返回 | 数据源 | 降级 |
|---|---|---|---|
| `_get_store()` L11 | FAQ 向量存储实例 | 容器 | 未初始化抛 RuntimeError |
| `@tool search_faq(query)` L16 | `{found,results,top_score}` | 向量库 | 空 query / 未初始化返回 `found:False` L34/39 |
| `@tool get_faq_categories()` L44 | 分类列表 | 向量库 | 未初始化返回 `[]` L50 |

**风险点**：薄封装，无检索超时/分数阈值控制，`store.search` 的语义完全依赖 `knowledge/vector_store.py`（未读，本任务范围外）。

### 5.2 `tools/api_client.py`（327 行，17 个函数/方法）

**一句话职责**：封装 `catalog.ustc.edu.cn` 公开 API（**全部无需认证**）+ 教学楼映射 + 5 分钟内存缓存。

**教学楼映射**：`BUILDING_CODE_MAP`（L19，code→中文名）、`BUILDING_ALIAS`（L39，简称→code，如"三教/高新A/信智楼"）；`resolve_building`（L51）、`building_name`（L72）、`building_short_name`（L77）。

**CatalogAPI（L86）端点**：

| 方法 | 端点 | 认证 |
|---|---|---|
| `get_timetable(date_str)` L196 | `/api/teach/timetable-public-all/{date}` | 无 |
| `get_semesters()` L214 | `/api/teach/semester/list` | 无 |
| `get_exams(semester_id)` L227 | `/api/teach/exam/list/{id}` | 无 |
| `get_general_exams(semester_id)` L242 | `/api/teach/general-exam/list/{id}` | 无 |
| `search_courses(keyword)` L257 | `/api/teach/course/search?keyword=` | 无 |
| `get_lessons(semester_id)` L272 | `/api/teach/lesson/list-for-teach/{id}` | 无 |
| `get_lesson_infos(codes,semester)` L287（POST） | `/api/teach/lesson/infos` | 无 |
| `get_current_semester()` L305 | 由 `get_semesters` 推断 | 无 |

**认证方式**：模块 docstring 明示"全部无需认证"；`__init__` 可选注入已认证 session（来自 CASClient，L100-106），`set_session`（L125）切换后清缓存。

**风险点**：
- **`_warmup` 是空实现**（L114-123）：注释声称"建立 cookie"，实际 `pass`；无外部 session 时认证类端点必然失败（当前 catalog 端点公开，暂不影响）。
- **缓存返回可变对象**：`_get/_post` 直接返回缓存中的 list/dict 引用（L145、L178），调用方若修改会污染缓存。
- **`get_current_semester` 依赖 API 排序**（L318-320）：假定列表按 start 降序，若 API 顺序变化则选错学期。
- **`resolve_building` 对 int 输入不校验**（L57-58）：直接透传，如 999 会流向后续逻辑。
- **`_post` 异常处理过粗**：统一 `except Exception`（L190），无超时/连接细分。
- 别名匹配用子串 `if alias in name`（L66-68），依赖字典插入顺序，极端输入可能误命中。

**统计**：327 行，17 个函数/方法。

---

## 6. `services/cas_client.py`（CAS 统一认证客户端，552 行，27 个方法/属性）

**一句话职责**：模拟科大 CAS（id.ustc.edu.cn）登录（表单 RSA / ticket 重定向两种方式），维护认证 session 并封装 jw 内部 API。

**登录方式**：
- **表单登录** `login(username,password,service)` L175：GET 登录页 → 提取 `execution` + RSA 公钥 → `PKCS1_v1_5` 加密密码 → POST；成功判定用 `"login" not in resp.url` 启发式（L247）。
- **重定向/ticket** `get_login_url` L71 → `validate_ticket` L86（`/cas/serviceValidate`，正则解析 `<cas:user>`）→ `login_with_ticket` L121（验证 ticket → 建 jw session → 建 catalog session → 取学生信息）。

**状态**：`_logged_in`/`_student_id`/`_student_data_id` 三个属性（L48-50），`is_logged_in`/`student_id`/`student_data_id` property（L57-67）。

**加密与 session**：`_encrypt_password` L337（`RSA.import_key` + `PKCS1_v1_5` + base64）；`_session = requests.Session()`（L43），`logout` 仅清 cookie 与状态（L546-552）。

**jw 内部 API 方法**：`get_course_table` L450、`get_course_table_by_week` L463、`get_grade_semesters` L481、`get_grades` L488、`get_course_selection` L497、`get_program_modules` L505、`get_my_program_tree` L515、`get_student_info` L391。

**`get_my_program_tree` 链路**（L515-544）：GET `/for-std/program` → 302 到 info 页 → 正则 `r"'hasAttachment':null,'id':(\d+),'logs'"` 提取 programId → GET `/for-std/program/root-module-json/{id}` 返回模块树。

**风险点**：
- **正则极脆弱**：`get_my_program_tree` 依赖精确 HTML 序列化 `'hasAttachment':null,'id':...,'logs'`（L536）；`_extract_execution`/`_extract_rsa_key`（L318-335）、`_extract_data_id`（L432-446）、`get_student_info` 姓名正则（L421-423）都依赖页面 DOM，任意改版即失效。
- **加密弱密码学**：`PKCS1_v1_5`（L343）非 IND-CCA2 安全（Bleichenbacher 类攻击面），虽为 CAS 服务端要求，但属已知弱点。
- **登录状态不一致**：`login_with_ticket` 在 L138 先设 `_student_id`，若后续 jw session 建立失败返回 False（L170-171），留下"有学号但未登录"的中间态；`login` 成功判定 `"login" not in ...` 也是脆弱启发式。
- **错误契约不一致**：`get()` 未登录抛 `RuntimeError`（L292-293），`get_json()` 未登录返回 `{"error":"未登录"}`（L304-305），调用方需分情况处理。
- **无会话持久化/线程安全**：单 session 内存态，Streamlit 多用户并发会互相干扰；`logout` 不清 `_home_page_html`。
- 密码明文在内存中短暂存在（登录过程），属设计必要但需注意日志不落库。

**统计**：552 行，27 个方法/属性（23 方法 + 4 property）。

---

## 7. `services/young_client.py`（青春科大活动客户端，166 行，13 个函数/方法）

**一句话职责**：封装 young.ustc.edu.cn 加密 HTTP 协议拉取"报名中"活动，Provider 抽象预留官方 API 替换。

**Provider 抽象**：`BaseYoungProvider` L86（唯一抽象方法 `fetch_enrolment_activities`）→ `EncryptedHttpProvider` L93（当前唯一实现）→ `YoungService` L153（入口，`from_token` L159）。

**加密（pycryptodome 用途）**：`Crypto.Cipher.AES`（`_encrypt` L103）做 AES-128-CBC + ZeroPadding，`key = token[-32:][16:32]`、`iv = token[-32:][0:16]`（L99-101）；请求头仅 `X-Access-Token`（L118），GET 参数 `_t`(毫秒) + `requestParams`(百分号编码 base64 密文)（L116）。

**`fetch_enrolment_activities`**（L125）：GET `/mobile/item/enrolmentList`，`pageNo=1,pageSize=50`，解析 `result.records` 为 `YoungActivity` dataclass（L49，含 `pic_url/apply_deadline/start_dt/end_dt` property）。

**风险点**：
- **"加密"实为混淆**：key/iv 直接由 token 后 32 位切片得到（L99-101），token 既是认证又是密钥材料，确定性对称加密，非真正安全层（客户端可逆）。
- **token 长度无校验**：`token[-32:]` 静默截断短 token（L99），短于 32 位会导致 key/iv 切片不足 16 字节，AES 抛异常，缺前置校验。
- **分页未实现**：docstring 称"分页取满 page_size 条"，实际只取 `pageNo=1`（L128），超过 50 条的活动被遗漏，无翻页循环。
- **字段类型假设**：`int(r.get("favCount") or 0)`（L144）遇非数字字符串（如 "1.2k"）直接 ValueError 使整次拉取失败，无 try/except。
- 业务失败 `raise RuntimeError`（L122）向上抛，`fetch_enrolment_activities` 未捕获，调用方需自行处理。

**统计**：166 行，13 个函数/方法（含 dataclass 与 property）。

---

## 8. `services/activity_recommender.py`（活动推荐引擎，271 行，17 个函数/方法）

**一句话职责**：规则加权（紧迫度 0.40 + 空闲匹配 0.35 + 热度 0.25）+ MMR 多样性重排的冷启动活动推荐，附可解释理由。

**关键组件**：

| 函数/类 | 行为 |
|---|---|
| `FreeTimeMatcher` L37 | 由 `student_courses` 构建每周课程时段；`from_db` L44、`occupied_on` L66、`free_ratio` L77、`free_days` L91 |
| `score_urgency(act,now)` L105 | 报名截止紧迫度 0~1（≤1天=1.0、≤3天=0.85…；无截止=0.3） |
| `score_freetime(act,matcher,now)` L124 | 分层：短活动(≤3天)看时段重叠比例；长活动看"可参与日"占比；无时间=0.5 |
| `score_hotness(act)` L139 | `0.6*log1p(收藏) + 0.4*log1p(参与人数)`，min-max 归一化在 recommend 内做 |
| `recommend(activities,matcher,now,top_n,lambda_)` L183 | 过滤过期→三因子打分→MMR 贪心重排（`λ*相关度-(1-λ)*相似度`）→附理由 |
| `BaseRanker` L253 / `RuleRanker` L260 | 抽象 rank 接口，RuleRanker 为当前默认实现 |

**相似度** `_similarity` L144：同类别 1.0 / 同模块 0.6 / 同组织 0.4 / 其他 0（均空字符串有守卫）。

**风险点**：
- **`free_ratio` 跨天 bug 隐患**：L85 只用 `occupied_on(start)`（取活动**开始日**课表）与整个活动区间比较；短活动若跨 ≤3 天且跨日，忙碌时段只算了起始日，空闲比被高估。
- **单日课表重复累积**：`occupied_on` 遍历 `by_weekday` 里同一天的所有时段（L70-74），若 student_courses 有多条同一天记录会正确累加，但重叠时段未去重，`busy` 可能重复计数导致空闲比偏低。
- **热度归一化边界**：`hmax==hmin` 时全体置 0.5（L214-217），所有活动热度等价，属合理但非显式文档。
- **`recommend` 静默过滤**：过期活动直接丢弃（L199-203），无日志/计数返回，调用方无法感知过滤了多少。
- `_build_reason` 中 `days` 浮点取整（L169）在临界值可能显示偏差。

**统计**：271 行，17 个函数/方法。

---

## 跨文件共性问题（汇总）

1. **`_norm_course_name` 三处重复且语义不一**：`advisor_tools.py:50`、`course_tools.py:52`、`program_tools.py:35`；advisor 版本未用 `isspace()` 全量去空白，易出现课程名匹配分歧。
2. **`_resolve_program` 双实现不一致**：`advisor_tools.py:315`（带 `_prog_priority`）vs `program_tools.py:67`（无优先级），注释均称"一致"，实际不同。
3. **全局可变状态**：`advisor_tools.py:87` 的 `_current_profile`、`ServiceContainer()` 单例、`CASClient` 单 session，均非线程安全，Streamlit 多用户下有跨用户污染风险。
4. **越权/隐私**：`course_tools._is_locked` 忽略 `student_id`（L45），fallback 按传入学号查库；`schedule_tools` 增查无登录校验。
5. **硬编码易失效数据**：`schedule_tools.py:226` 开学日期、`course_tools.py:927/1049` fallback 学期与模块、`advisor_tools.py` 的 8/9 月分界。
6. **脆弱 HTML 正则**：`cas_client.py` 多处（L318-335、L432-446、L536）依赖页面序列化格式。
7. **连接/异常治理**：`advisor_tools` 手动 `conn.close()` 无 `finally`；`course_tools` 多处裸 `except: pass`。
8. **来源标识不统一**：course_tools 有 `source=real/fallback/locked`，advisor_tools/schedule_tools/faq_tools 无 source 字段。
