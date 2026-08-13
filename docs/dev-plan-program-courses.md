# 培养方案 × 选课结合实施计划（任务 1-4 分工文档）

> 指挥中心拆解文档（2026-08-13，经用户逐项确认）。
> 任务 1/3 由 Claude Code 执行，任务 2/4 由 Codex 执行，四路并行（文件互不冲突）。
> 验收顺序：四任务产出 → 指挥中心总验收（数据核对、回归、页面 e2e、git 提交）。

---

## 背景与已确认决策（全部经用户确认，无需再议）

培养方案数据（jw 官方 1201 个，含必修/学期/模块）已入库 `data/course_data.db`，
现与选课推荐深度结合，四项能力全部实施：

1. **推荐排序深度融合**：推荐拆为「必修组 + 选修组」两组，必修组前置。
2. **培养进度追踪与缺口推荐**：独立页面展示已获/要求学分、必修缺口。
3. **学期规划与先修路径**：按方案 term 排出「第 X 学年该修什么」（先修关系不爬库，
   页面提示“先修要求以培养方案为准”）。
4. **培养方案查看与问答**：三合一页面 = 我的方案（必修清单/学分要求/模块构成）+
   学期规划 + 进度概览。

关键规则（必须遵守）：

- **数据来源混合**：登录后（session 有 user.id/major/grade）用真实数据 + 成绩单
  （`xiaowo.db` 的 `student_grades` 表，已由 `course_tools._fetch_real_grades` 同步）；
  未登录用手动画像（major/grade/兴趣）降级。
- **方案匹配**：登录后优先学生**个人方案**（`cas_client.get_program_modules`，注意其
  返回模块树结构与全量库 program_courses 不同，需要解析适配，见任务 3）；
  未登录用全量库按年级匹配：同年级方案 → 最近低年级 → 最新方案。
- **清除过时方案**：全量库只保留 2022 级及以后（`grade >= "2022级"`）。
  现状：programs 1201 → 632，program_courses 73672 → 45168（2015~2021 共 569 个方案、
  28504 行课程待清）。爬虫源数据 `scripts/data/programs_jw/` **保持全量不动**，过滤在入库时做。
- **排序规则**：
  - 必修组 = 学生方案内 `required=必修` 且未修的课程，**学期紧迫度优先**
    （当前学年该修的优先；已过期应修未修置顶），同类内按评分降序；
  - 选修组 = 方案内选修 + 方案外全部课程（通识/任选/拓展），按评分降序；
  - 两组分开返回与展示；默认 `max_results=10`（6 必修 + 4 选修，不足互补）；
  - 保留现有偏好因子（冲分/硬核/兴趣）与理由生成、评分/评论/教师等全部现有字段。
- **页面入口**：侧边栏「模块切换」radio 新增「培养方案」选项，选中时主区渲染三合一
  页面（其余模块保持聊天）；未登录时方案页显示按画像年级匹配的全量库方案，
  进度区提示「登录后查看个人进度」。

---

## 任务 1（Claude Code）：入库年级过滤 + 重建 course_data.db

### 目标
`scripts/build_course_db.py` 增加“只入 2022 级及以上方案”的过滤，重建
`data/course_data.db`，使 programs=632、program_courses≈45168。

### 改造点（只改 build_course_db.py）
1. 新增常量 `MIN_PROGRAM_GRADE = 2022`（入学年级下限，闭区间）。
2. `load_programs()` 加载后过滤：
   `int(str(p.get("grade","")).rstrip("级")[:4]) >= MIN_PROGRAM_GRADE`，
   打印“过滤掉 N 个旧方案”。
3. 其余逻辑（courses/reviews/course_rates/course_terms/teachers 构建、课程匹配、
   第 6 步 programs/program_courses 插入）**全部保持不变**。
4. 运行 `py -3 -X utf8 scripts/build_course_db.py` 重建。

### 验收标准
- 构建日志：`programs: 632, program_courses: 45168`（±2%），匹配率仍 > 90%。
- `courses: 2351`、`reviews: 44403` 等与上次一致（无回归）。
- 抽查：`SELECT COUNT(*) FROM programs WHERE grade < '2022级'` = 0。
- `py -3 -m py_compile scripts/build_course_db.py` 通过。

---

## 任务 2（Codex）：recommend_courses 分组重构

### 目标
`tools/advisor_tools.py` 的 `recommend_courses` 改为「必修组 + 选修组」两段式推荐。

### 接口变化（兼容旧调用）
- 新增可选参数：
  - `taken_courses: list[str] = None` — 已修课程名列表（登录后由上层从
    `student_grades` 读取传入；未登录为 None，视为全部未修）；
  - `current_year_index: int = None` — 当前学年（1=大一，2=大二…）。None 时按
    profile.grade 推算（"大二"→2；"2024级"→当前日期所在学年，可用
    `utils.time_parser` 或简单按学年计算：2026 秋为 2026-2027 学年）；
  - `current_term: str = None` — 当前学期标识（如 "2026秋"），None 时由当前日期推断。
- 返回结构新增顶层 `groups: {"required": [...], "elective": [...]}` 与
  `progress` 摘要（可选，见下）；`recommendations` 保持旧格式 = 必修组 + 选修组
  拼接（向后兼容），每组条目仍含现有全部字段（name/code/credit/dept/rating_avg/
  rate_count/dims/teachers/multi_teacher/terms/top_reviews/program_hint/reasons）。

### 实现要点
1. **方案定位**（复用并增强 `_program_hint` 思路，建议新增内部函数
   `_resolve_program(conn, major, grade)` 返回 `(program_id, program_name)`）：
   - 匹配 `programs`：`college LIKE %major% OR name LIKE %major%`，
     `grade` 优先同年级 → 最近低年级 → 最新（SQL `ORDER BY` 或 Python 内排序）；
   - 无 major 时不分组（保持现状纯评分推荐）。
2. **必修组候选**：`program_courses JOIN courses`（course_id 非空）、
   `required='必修'`、且课程名不在 `taken_courses`（归一化比较，复用
   `_norm_course_name`）。
3. **学期紧迫度排序**（必修组内）：
   - 解析方案 term（如 "2秋"）→ 学年号（term 首字符）；
   - 等级：已过期（学年号 < current_year_index）置顶 → 当前学年该修
     （== current_year_index）其次 → 未来学期最后；同级内按 rating_avg 降序；
   - term 为空或无法解析的按当前学年档处理。
4. **选修组候选**：方案内 `required='选修'` + 方案外全部（现有评分候选池减去
   必修组已选与已修），按 rating_avg 降序。
5. **条数**：默认 10（6+4），`max_results` 参数仍生效（>=2 时按 60%/40% 拆分，
   不足互补；<=1 时全部给第一组）。
6. **进度摘要**（`progress` 字段，返回给上层展示）：已修必修数/方案必修总数、
   已修学分数（从 taken_courses 无法得学分，用方案行 credit 汇总已修课程名匹配项）、
   缺失时置 None。
7. 保留 `keyword_fallback`、`profile_note`、`filtered_count` 等现有字段语义。
8. 更新 docstring（含新参数说明）。不修改其他文件。

### 验收标准
- `py -3 -m py_compile` 通过；`scripts/verify_tools.py`、`scripts/test_fixes.py`、
  `scripts/advisor_smoke.py` 全量回归通过（若断言依赖旧返回结构，允许微调断言，
  但行为语义不得破坏）。
- 手工冒烟（临时脚本，不提交）：major="计算机科学与技术"、grade="2024级"、
  taken_courses=["高等数学(1)"]、current_year_index=3：
  - 返回的必修组课程全部来自 2024 级（或最近）计算机方案且 required=必修；
  - 必修组中已过期的（如方案中 1 学年的课）排在当前学年课之前（若存在）；
  - 选修组无必修课程；两组不重叠；总条数=10（或 max_results）。

---

## 任务 3（Claude Code）：新建 program_tools + agent 接线

### 目标
新建 `tools/program_tools.py`，提供三个工具供培养方案页面与 QA 流程调用；
并注册进 QA 工具表（`agents/qa/graph.py` 或等价工具注册处，先 grep 确认注册方式，
参考现有 tools 如何注册；只加新工具，不动其他工具）。

### 工具定义（@tool 装饰器，风格与 course_tools/advisor_tools 一致）
1. `get_my_program(major: str, grade: str = None) -> dict`
   - 全量库定位方案（同任务 2 规则：同年级→最近低年级→最新）；
   - 返回：`{program_id, name, college, grade, totalCredits(program_courses 中
     credit 汇总或 programs 无该字段则 None), modules: [{category, required_credits
     (该 category 下课程学分和，若无 category 则按一级模块名), course_count}],
     courses: [{code,name,required,credit,term,category}]}`；
   - courses 按 category 分组返回（供页面按模块展示）。
2. `get_program_progress(major: str, grade: str = None,
   taken_courses: list[str] = None, taken_credits: list[float] = None) -> dict`
   - 已修判定：taken_courses 归一化匹配方案课程名（复用课程名归一化思路，
     不依赖 advisor_tools 内部函数，独立实现或从 utils 提取）；
   - 返回：`{program_id, name, required_total, required_taken, required_remaining:
     [{code,name,credit,term,category}], credits_taken, credits_required,
     percent, modules_progress: [{category, taken, total}]}`；
   - taken_credits 缺省时按方案行 credit 估算。
3. `plan_semester(major: str, grade: str = None, year_index: int = 1) -> dict`
   - 按方案 term 解析（"2秋"→ 第 2 学年 秋），返回第 year_index 学年的全部课程：
     `{year_index, terms: [{term: "2秋", courses: [{code,name,required,credit,category}]}],
     total_credits}`；term 无学年前缀的归入“未标注”分组。

### 登录态个人方案适配（可选增强，本次实现）
- `cas_client.get_program_modules(0)` 返回学生个人模块树（结构与全量库
  root-module-json 相同：节点 type.nameZh / planCourses[].course / readableTerms /
  compulsory）。三个工具都增加可选参数 `personal_tree: dict = None`：
  传入时**优先用个人方案树**（解析为与全量库相同的 courses 结构后走同一套逻辑）；
  未传入用全量库。解析函数 `_parse_tree(tree) -> list[dict]` 放本文件内。
- 页面层负责在登录后拉取个人树并传入（任务 4 对接）。

### 验收标准
- `py -3 -m py_compile` 通过；不破坏现有 verify_tools/test_fixes/advisor_smoke。
- 临时冒烟（不提交）：get_my_program("计算机科学与技术","2024级") 返回 2024 级
  方案且 courses 按 category 分组；get_program_progress 给定 taken_courses 后
  required_remaining 不含已修；plan_semester(...,2) 返回第 2 学年秋/春课程。
- 工具出现在 QA 工具表（grep 确认注册成功）。

---

## 任务 4（Codex）：UI 培养方案三合一页面 + 侧边栏模块

### 目标
- `ui/chat.py`：侧边栏「模块切换」radio 列表新增「培养方案」；
- `app.py`：selected_module == "培养方案" 时主区渲染新页面（不走聊天流程）；
- 新建 `ui/program_page.py`：三合一页面（我的方案 / 学期规划 / 进度概览）。

### 页面内容（program_page.py，风格与现有 ui 一致）
1. **数据准备**：
   - 已登录：从 `st.session_state.user`（major/grade）+ `tools.course_tools` 读
     `student_grades` 已修课程（复用现有读取函数，若有）；拉取个人方案树
     （`cas_client.get_program_modules(0)`，经 ServiceContainer 获取 cas_client，
     try/except 降级到全量库）传入 program_tools 三工具；
   - 未登录：用画像（若 session 有 major/grade 则用之，否则页面提示
     “先登录或填写专业年级后查看”）+ 全量库。
2. **页面布局**（Streamlit 原生组件即可，不引入新依赖）：
   - 顶部：方案名 + 年级 + 进度概览条（st.progress，已获学分/要求学分百分比；
     未登录显示提示文案）；
   - st.tabs(["我的方案", "学期规划", "进度概览"])：
     - 我的方案：按 category 分组的课程表（st.dataframe 或表格，
       列：课程名/代码/学分/必修/学期/模块）；
     - 学期规划：st.selectbox 选第 N 学年（1-4+），展示该学年秋/春课程表 +
       总学分；未标注学期的课程列表；
     - 进度概览：已修必修/必修总数、缺口清单（未修必修课程表，按学期紧迫度排序
       可复用任务 2 排序思路或简单按 term 排）、各模块进度（progress bar 列表）。
3. **错误降级**：数据库/登录态异常时页面显示友好提示，不崩溃。

### 验收标准
- `py -3 -m py_compile` 全部改动文件通过；
- 手工冒烟：`py -3 -m streamlit run app.py --server.port 8501` 启动，
  侧边栏可选「培养方案」；未登录时页面显示提示 + 全量方案（若会话有画像）；
  页面无异常堆栈（日志无 ERROR）。

---

## 总验收（指挥中心执行，四任务无需关心）

1. 数据核对：programs=632、program_courses≈45168、无 <2022 方案。
2. 回归：verify_tools / test_fixes / advisor_smoke 全过；browser_e2e 全场景通过。
3. 新功能冒烟（真实库）：推荐返回 groups 分组正确；三合一页面渲染正常。
4. git 提交（xiaowo-dev 身份）并推送。

## 注意事项（四任务共同遵守）
- 只改自己任务的文件；`scripts/data/programs_jw/`（爬虫源数据）**不动、不提交**。
- 不提交 cookie/profile；不打印任何会话内容。
- 代码风格：模块 docstring、中文注释、与现有文件一致；无外部新依赖。
- 不要关闭 127.0.0.1:9223 的 Edge 窗口（登录态浏览器，本阶段只读备用）。
- 长任务期间每 5 分钟检查一次产出/日志，遇错先自行修复再继续。
