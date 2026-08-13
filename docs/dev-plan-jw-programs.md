# jw 培养方案全量爬取与入库（任务 A / 任务 B 分工文档）

> 指挥中心拆解文档。任务 A 由 Claude Code 执行，任务 B 由 Codex 执行（可并行开发，
> 验收顺序：A 产出数据 → B 用数据重建库 → 指挥中心总验收）。
> 数据源为教务系统 jw.ustc.edu.cn（登录态），API 规格已经实测确认（2026-08-13 探测）。

---

## 背景

项目 `f:\小蜗` 的选课推荐需要"培养方案弱标注"（课程属于哪个专业的方案、必修/选修、
开课学期）。现有数据来自 icourse.club 转贴（`scripts/data/programs/*.json`，1066 个），
但学期字段缺失、college 字段被导航文本污染。现改为爬取教务系统 jw.ustc.edu.cn 官方
培养方案（全部 1201 个本科主修方案，含 2015~2026 级），字段权威且带 `readableTerms`
（如 "2秋"）与 `compulsory`（必修标识）。

## 登录态（两个任务共用，只读使用）

- 已有一个 **Edge 浏览器窗口** 以独立 profile（`scripts/.jw_profile2/`）启动，
  并通过 `--remote-debugging-port=9223` 暴露 CDP，**已登录 jw.ustc.edu.cn**。
- **不要关闭该 Edge 窗口、不要重启浏览器、不要修改其 profile**。
- 会话 cookie 由浏览器维护，脚本通过 playwright CDP 连接即可带登录态访问：
  ```python
  from playwright.sync_api import sync_playwright
  p = sync_playwright().start()
  browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
  ctx = browser.contexts[0]   # 直接使用 ctx.request 发请求, 自动携带 cookie
  ```
- 禁止打印/输出/提交 cookie、token 等会话内容；`scripts/.jw_profile2/` 已在 .gitignore。

---

## 任务 A（Claude Code）：jw 培养方案爬虫

### 目标
新建 `scripts/crawl_jw_programs.py`，全量爬取 1201 个本科主修培养方案并落盘为
结构化 JSON，支持断点续爬与失败重试。

### API 规格（已实测，直接使用）

**1. 方案列表（分页）**
```
GET https://jw.ustc.edu.cn/for-std/program-search/search?type=MAJOR&queryPage__={page},{size}&_={timestamp}
```
- 分页参数是 `queryPage__=页码,每页条数`（如 `queryPage__=1,50`）；`_` 为毫秒时间戳。
- 响应 `{"data": [...], "_page_": {"currentPage":1,"rowsInPage":20,"rowsPerPage":20,"totalRows":1201,"totalPages":25}}`
  （size=50 时 25 页；totalRows 固定 1201）。
- `data[]` 元素关键字段：
  ```
  data[i].program = { id, nameZh, nameEn, grade("2015"), type("MAJOR"),
                      bizType:{id,nameZh("本科")}, department:{id,nameZh("000少年班学院"),code,abbrZh,simpleNameZh},
                      major:{id,nameZh("00002少年班数学类"),code}, majorDirection,
                      stdType:{nameZh("普通")}, education:{nameZh("本科")} }
  ```
- 只爬 `type=MAJOR`（主修）即可，totalRows=1201。

**2. 方案详情（模块树）**
```
GET https://jw.ustc.edu.cn/for-std/program-search/root-module-json/{program_id}
```
- 响应为嵌套模块树：
  ```
  根 = { id, requireInfo:{requiredCredits:160.0, requiredSubModuleNum, requiredCourseNum},
         children:[ 模块节点 ] }
  模块节点 = { id, type:{nameZh:"学科群基础课程"|"必修"|"通修课程"|"自由选修课"|...},
               requireInfo, planCourses:[...], children:[ 子模块 ] }
  ```
- 课程在 `planCourses[]`，每个元素关键字段：
  ```
  { id, compulsory:true, terms:["TERM_5"], suggestTerms:[],
    readableTerms:["2秋"], readableSuggestTerms:["2016秋"],
    remark, periodInfo:{total:80, weeks:18, theory:80, practice, experiment,...},
    course:{ id, nameZh:"微分方程I", nameEn, code:"001355", ...（含学分字段, 实测确认字段名, 常见 credit/credits）},
    openDepartment, preCourses, examMode, teachLang, termTextZhs, termTextEns }
  ```
- `compulsory` 为布尔：true=必修, false=选修。

### 输出格式（每个方案一个文件）
`scripts/data/programs_jw/{pid}.json`，格式（与 build_course_db 现有 programs 输入兼容，字段对齐）：
```json
{
  "pid": 1012,
  "name": "少年班数学平台专业培养方案",
  "nameEn": null,
  "grade": "2015级",
  "bizType": "本科",
  "department": "000少年班学院",
  "major": "00002少年班数学类",
  "stdType": "普通",
  "education": "本科",
  "totalCredits": 160.0,
  "crawled_at": "2026-08-13 14:30:00",
  "courses": [
    {
      "code": "001355",
      "name": "微分方程I",
      "required": "必修",
      "exam": "",
      "credit": 4.0,
      "category": "学科群基础课程/必修",
      "term": "2秋"
    }
  ]
}
```
- `grade`：列表里是 "2015"，统一加"级"后缀（与现有数据 "2025级" 一致）。
- `required`：`compulsory ? "必修" : "选修"`。
- `term`：`readableTerms` 数组用 `,` 连接（可能多个如 "1秋,2春"；空则 `""`）。
- `category`：模块路径（父模块 type.nameZh + "/" + 叶子模块 type.nameZh；根直属课程用叶子模块名）。
- `credit`：从 `course` 对象中取学分（字段名以实测为准，找不到用 0）。
- `exam`：`examMode` 的文字（实测确认；找不到留空）。
- 模块树递归遍历，`planCourses` 为空或节点无课程则跳过。

### 技术要求
1. 会话：playwright `connect_over_cdp("http://127.0.0.1:9223")`，**用 `ctx.request` 发请求**，
   不要新建/关闭页面（该 Edge 窗口留给用户）。如 CDP 连接失败或请求返回未登录（HTML 登录页/302 到 CAS），
   打印明确错误并退出（由指挥中心处理重新登录）。
2. 限速与重试：每请求后 `time.sleep(random.uniform(0.2, 0.5))`；失败重试 3 次（退避 1s/2s/4s）；
   最终失败记录到 `scripts/data/programs_jw.log` 并继续（不中断全量）。
3. 断点续爬：`{pid}.json` 已存在则跳过（文件需先写完再落盘，避免半文件）。
4. 进度日志：每 100 个打印一次 `方案进度: {done}/{total} 失败 {failed}`；结束打印汇总。
5. 全程 `-X utf8` 运行；不要修改其他文件。

### 验收标准（任务 A 完成判定）
- `scripts/data/programs_jw/` 生成 1200+ 个 JSON（预期 1201，至少 1195）。
- 抽查 `1012.json`（少年班数学平台专业培养方案）：courses 非空、含 readableTerms 非空的课程、
  `required` 有"必修"/"选修"、`category` 含模块名、`grade`="2015级"。
- 日志显示失败数 ≤ 5。

---

## 任务 B（Codex）：build_course_db 接入 jw 培养方案并重建

### 目标
改造 `scripts/build_course_db.py`，使培养方案数据源切换为任务 A 产出的
`scripts/data/programs_jw/*.json`（替换现有 `scripts/data/programs/*.json`），
重新构建 `data/course_data.db`，使 `programs` / `program_courses` 表含 jw 权威数据
（含学期 term 与必修标识）。

### 现状（勿破坏）
- `scripts/build_course_db.py`：`PROG_DIR = PROJECT_ROOT / "scripts" / "data" / "programs"`，
  `load_programs()` 读取 `PROG_DIR/*.json`；第 6 步（约 L220-261）插入 programs/program_courses：
  - `programs(id, name, college, grade)`：id=pid, name=p["name"], college=p["college"], grade=p["grade"]
  - `program_courses(program_id, course_id, code, name, required, exam, credit, category, term)`
  - 课程匹配：先 code 精确（含去前导 0），再 name 相等，再 name LIKE 前缀，未匹配 course_id=NULL 仍入库。
- `database/schema_course.sql`：programs 表 (id INTEGER PRIMARY KEY, name, college, grade)；
  program_courses 表字段如上（TEXT 为主，无需改 schema，除非确认需要）。

### 改造点
1. `PROG_DIR` 改为 `scripts/data/programs_jw`（或新增 `load_programs_jw()` 读取该目录）。
2. 字段映射（与任务 A 输出对齐）：
   - `name` ← `name`、`college` ← `department`、`grade` ← `grade`（已是 "2015级" 格式）。
   - program_courses：`code`←code、`name`←name、`required`←required、`exam`←exam、
     `credit`←credit、`category`←category、`term`←term（如 "2秋"）。
3. 保持现有匹配逻辑（code → name 相等 → name 前缀）不变；打印匹配率。
4. 重建 `data/course_data.db`（脚本本来就全量重建，`--db` 默认路径即可）。
   确认 courses/reviews/teachers 等既有逻辑无改动、行数与上一次构建一致
   （courses≈2351、reviews≈44370、course_rates、course_terms、teachers/course_teachers）。

### 验收标准（任务 B 完成判定）
- 重建后 `programs` 行数 ≈ 1201（实际爬取数），`program_courses` 行数 > 50000。
- 抽查：`program_courses` 中 term 非空比例 > 80%（jw 数据带 readableTerms）。
- `SELECT COUNT(*) FROM program_courses WHERE term != ''` 打印验证。
- 示例验证：数学与应用数学专业培养方案（2025级）中 "数学分析(B1)" 或 "数学分析" 相关课程的 term 非空。

---

## 总验收（指挥中心执行，两个任务无需关心）

1. 真实库 `_program_hint` 验证：推荐场景中课程能带出 必修/学期 标注。
2. `scripts/verify_tools.py`、`scripts/test_fixes.py`、`scripts/advisor_smoke.py` 回归。
3. 浏览器端到端（`scripts/browser_e2e.py` 9 场景）。
4. git 提交（xiaowo-dev 身份）并推送。

## 注意事项（两个任务都遵守）
- 只改自己的目标文件；不碰 `tools/`、`agents/`、`services/`、`ui/`。
- 不提交 `scripts/data/`、`scripts/.jw_profile*/`（已 gitignore）。
- 代码风格：模块 docstring、中文注释、与现有文件一致；无外部新依赖（playwright/requests 已有）。
