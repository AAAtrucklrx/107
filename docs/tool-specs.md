# 🐌 小蜗 — Tool 详细规格说明

> 本文档逐一定义每个 Tool 的当前实现、升级方案和 Plan-and-Execute 兼容性。
> 最后更新：2026-08-25（同步推荐语义、个人培养方案来源、工具数量与当前验证口径）

---

## 模块一：智能问答（faq_tools.py）— 2 个 Tool

### 1. search_faq

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/faq_tools.py` |
| **当前状态** | ✅ 生产可用 |
| **升级计划** | 不变 |

**功能**：在 ChromaDB 向量知识库中搜索与用户问题最相关的 FAQ 文档。

**参数**：
```python
query: str  # 用户自然语言问题，如 "学生证怎么补办"
```

**返回值**：
```python
{
    "found": True,               # 是否找到相关结果
    "results": [
        {
            "content": "...",    # FAQ文档内容
            "score": 0.85,       # 相似度分数
            "source": "...",     # 来源文件名
            "category": "教务",  # 分类
            "is_official": True  # 是否官方来源
        },
        ...  # 最多 FAQ_TOP_K=5 条
    ],
    "top_score": 0.85            # 最高分（供Agent判断结果可信度）
}
```

**数据流**：
```
用户问题 → FAQVectorStore.search(query)
         → ChromaDB 向量检索（50 篇 Markdown 文档，5 分类）
         → 返回 top-K 结果 + 相似度分数
```

**已知问题**：
- Embedding 三层降级：校内 API（`qwen3-embedding`，实测可用，4096 维）→ 本地 `shibing624/text2vec-base-chinese` → 关键词 fallback
- 相似度阈值按模式校准（`THRESHOLD_MAP`：api=0.42 / local=0.35 / fallback=0.25），`FAQ_SIMILARITY_THRESHOLD=0.6` 仅作未知模式兜底

**Plan-and-Execute 兼容性**：
- Executor 可直接调用，无需占位符依赖
- Planner 通常在 Step 1 调用此 Tool 获取基础信息

---

### 2. get_faq_categories

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/faq_tools.py` |
| **当前状态** | ✅ 生产可用 |
| **升级计划** | 不变 |

**功能**：返回知识库中所有 FAQ 的分类列表。

**参数**：无

**返回值**：
```python
["办事", "教务", "生活", "科研与升学"]  # 对应 knowledge/data/ 下的子目录
```

**Plan-and-Execute 兼容性**：
- 辅助性 Tool，Planner 极少单独调用
- 可作为"引导用户提问"的辅助步骤

---

## 模块二：课业助手（course_tools.py）— 5 个 Tool + 2 新增

### 3. query_schedule

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/course_tools.py` |
| **当前状态** | ✅ CAS 实时课表 + SQLite 明示缓存降级；Demo 为合成数据 |
| **升级计划** | 真实 CAS 部署取决于可信 HTTPS 域名与白名单；不在前端猜测缺失排课 |

**功能**：查询当前认证学生的全学期课表。优先从 jw 内部 API 获取真实数据，将每门课的多段排课结构化并同步 SQLite；接口不可用时仅返回带来源提示的当前用户缓存。

**参数**：
```python
student_id: str = None  # 学号（登录用户；未登录时查询锁定）
week: int = None        # 兼容参数；工具层不据此裁剪，Web 端按结构化 week_numbers 切周
day: str = None         # 星期几，如 "周一"（可选）
```

**返回值**：
```python
{
    "student_id": "PB20240001",
    "courses": [
        {
            "course_code": "MATH1002",
            "course_name": "数学分析B2",
            "teacher": "李教授",
            "credits": 5,
            "time": "周一 1~16周 第3-4节;周三 1~16周 第1-2节",
            "location": "三教3A101",
            "semester": "2026-2027-1",
            "meetings": [
                {
                    "weekday": 1,
                    "week_numbers": [1, 2, 3, 4],
                    "periods": [3, 4],
                    "period_label": "第3-4节",
                    "start_time": "09:45",
                    "end_time": "11:20",
                    "location": "三教3A101"
                }
            ]
        },
        ...
    ],
    "count": 5,
    "source": "real"  # 或 fallback / locked
}
```

**Web 投影**：`GET /api/v1/academic/schedule` 只接受当前服务端 principal，不接受客户端学号。它补充 `semester_code / semester_start / total_weeks / current_week`，并把无法确认星期、周次或起止时间的记录放入 `unparsed_courses`。Web 周视图只绘制完整 meeting；不完整记录如实显示为“待确认”。

**时间映射**：官方 1–13 小节由 `utils/course_periods.py` 统一映射，首末小节决定实际起止时间；3–5、8–10 和 11–13 不得截断为固定两小节。多段、单双周、不连续周与显式时钟由 `utils/schedule_parse.py` 解析。

**Plan-and-Execute 兼容性**：
- 常作为 Planner 的 Step 1（"先查课表再做安排"）
- `count` 字段供后续 Step 判断"本学期有几门课"

---

### 4. find_empty_room

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/course_tools.py` |
| **当前状态** | ⚠️ 模拟数据（`i % 3 != 0` 随机生成空教室） |
| **升级计划** | ★ B3 — 对接教室占用 API |

**功能**：查找指定教学楼在指定时段的空教室。

**参数**：
```python
building: str    # 教学楼名称，如 "三教"、"五教"
time_desc: str   # 自然语言时间，如 "今天下午"、"周二上午"
```

**返回值**：
```python
{
    "building": "三教",
    "time": "下午(14:00-18:00)",
    "empty_rooms": [
        {"room": "三教3A104", "free_slots": "14:00-17:35", "capacity": 100},
        ...  # 最多5间
    ],
    "count": 3
}
```

**降级路径说明（仅 catalog API 不可用时触发）**：
1. 教室列表从 `student_courses` 的 location 字段提取 → 只有种子数据的教室
2. 手动补充 4 间模拟教室（`f"{building}3A103"` 等）
3. 空闲判断用 `i % 3 != 0` 硬编码，不真实
4. `capacity` 用 `80 + i*20` 计算，不真实

> 主路径已接入 catalog 真实教室占用 API（B3 已实施）；上述模拟逻辑仅存在于 fallback 分支，且返回结果带 `source="fallback"` 与 `message` 来源提示

**★ B3 升级方案**：
```
真实数据源：CatalogAPI.get_timetable(date)
API: /api/teach/timetable-public-all/{date}
流程：
  1. 解析 time_desc → 具体日期 + 时段
  2. 调用 API 获取该日全校教室占用
  3. 按 building 过滤
  4. 取反集：该教学楼全部教室 - 被占用教室 = 空教室
  5. 按时段过滤（上午/下午/晚上）

已知限制：
  - API 仅返回有排课的教室，不返回空教室列表
  - 需要从排课数据中推断全部教室列表
  - 暑假期间数据稀疏（夏季学期仅9节课）
```

**参数变更建议**：
```python
# 当前：building + time_desc
# 建议新增可选参数：
building: str = None     # 不指定则返回所有教学楼的
date_str: str = None     # 精确日期（ISO格式），替代 time_desc 中的日期解析
```

**Plan-and-Execute 兼容性**：
- 独立 Tool，通常不依赖其他 Step 的结果
- 返回值中 `empty_rooms` 列表可被后续 Step 引用（如"帮我预约空教室"）

---

### 5. query_grade

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/course_tools.py` |
| **当前状态** | ⚠️ 模拟数据（student_grades 表仅上学期种子数据） |
| **升级计划** | 暂保留（需登录教务系统，C阶段再对接） |

**功能**：查询学生成绩。

**参数**：
```python
student_id: str = None      # 学号
course_name: str = None     # 课程名（模糊匹配）
semester: str = None        # 学期，如 "2025-2026-1"
```

**返回值**：
```python
{
    "student_id": "PB20240001",
    "grades": [
        {"course_name": "数学分析B1", "credits": 4, "score": 88, "grade_point": 3.7, "semester": "2025-2026-1"},
        ...
    ],
    "count": 6
}
```

**内部实现**：调用 `_query_grades()` 内部函数，被 `query_grade` 和 `calc_gpa` 共用。

**暂不升级原因**：
- 成绩数据存在教务系统（https://jwxt.ustc.edu.cn），需要 CAS 认证
- 当前种子数据足够支撑 GPA 计算和选课推荐的 Demo

---

### 6. calc_gpa

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/course_tools.py` |
| **当前状态** | ✅ 计算逻辑正确，数据为模拟 |
| **升级计划** | 暂保留（依赖 query_grade 的真实数据） |

**功能**：计算 GPA（科大 4.3 制）。

**参数**：
```python
student_id: str = None  # 学号
semester: str = None    # 学期（可选，默认全部学期累计）
```

**返回值**：
```python
{
    "student_id": "PB20240001",
    "semester": "全部学期",
    "gpa": 3.26,
    "total_credits": 19,
    "details": [...]   # 原始成绩列表
}
```

**计算逻辑**（`utils/gpa_calculator.py`）：
```
GPA = sum(grade_point × credits) / sum(credits)
科大4.3制对照表（教字〔2019〕14号）：100~95→4.3, 94~90→4.0, 89~85→3.7, 84~82→3.3, 81~78→3.0, ...
```

**当前种子数据的 GPA**：
- 数学分析B1(4学分,3.7) + 线性代数B1(3学分,4.0) + 力学(4学分,3.0)
- + 大学英语II(2学分,3.3) + 程序设计(4学分,2.3) + 近现代史(2学分,3.7)
- = GPA 3.26 / 19学分

**Plan-and-Execute 兼容性**：
- 返回的 `gpa` 是高频引用的 fact
- Context 自动提取 `gpa` → 后续 Step 可用 `{step_N.gpa}` 占位

---

### 7. query_exam

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/course_tools.py` |
| **当前状态** | ⚠️ 模拟数据（用 today+14天+i*2天 生成假日期） |
| **升级计划** | ★ B4 — 对接考试 API |

**功能**：查询考试安排。

**参数**：
```python
student_id: str = None      # 学号
course_name: str = None     # 课程名（可选过滤）
```

**返回值**：
```python
{
    "exams": [
        {
            "course": "数学分析B2",
            "date": "2026-08-14",
            "time": "14:00-16:00",
            "location": "三教3A101",
            "type": "期末考试"
        },
        ...
    ]
}
```

**★ B4 升级方案**：
```
真实数据源：CatalogAPI.get_exams(semester_id)
API: /api/teach/exam/list/{semesterId}
流程：
  1. 获取当前学期 semesterId（从 get_semester_list 匹配）
  2. 调用 API 获取全校考试安排
  3. 与学生选课列表交叉匹配（取交集）
  4. 返回该学生的考试信息
```

---

### 8. search_courses ★ 新增

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/course_tools.py` |
| **当前状态** | 不存在 |
| **升级计划** | ★ B阶段新增 |

**功能**：在真实课程库中按关键词搜索课程。

**参数**：
```python
keyword: str              # 搜索关键词，如 "机器学习"、"英语"
semester: str = None      # 学期（可选）
limit: int = 10           # 返回数量上限
```

**返回值**：
```python
{
    "keyword": "机器学习",
    "courses": [
        {
            "course_code": "CS2001",
            "course_name": "机器学习导论",
            "teacher": "王教授",
            "credits": 3,
            "time": "...",
            "location": "..."
        },
        ...
    ],
    "count": 3
}
```

**数据来源**：
```
CatalogAPI.search_courses(keyword) → POST /api/teach/course/search
fallback: SELECT * FROM courses WHERE name LIKE ? OR teacher LIKE ?
```

**新增理由**：
- 现有 `query_schedule` 只查已选课程
- Planner 需要"搜索课程"能力来支撑"帮我找一门关于XX的课"场景
- 对接真实 API 后数据量远大于种子数据

---

### 9. get_semester_list ★ 新增

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/course_tools.py` |
| **当前状态** | 不存在 |
| **升级计划** | ★ B阶段新增 |

**功能**：获取可用的学期列表。

**参数**：无（或 `limit: int = 10`）

**返回值**：
```python
{
    "semesters": [
        {"id": "2025-2026-2", "name": "2025-2026学年第二学期", "is_current": True},
        {"id": "2025-2026-1", "name": "2025-2026学年第一学期", "is_current": False},
        ...
    ],
    "current_semester": "2025-2026-2"
}
```

**数据来源**：`CatalogAPI.get_semesters()` → `/api/teach/semester/list`

**新增理由**：
- `query_schedule`、`query_exam` 等 Tool 需要 semester 参数
- 用户说"上学期GPA"时需要知道学期列表
- Planner 需要先获取学期列表才能确定 API 参数

---

## 模块三：选课顾问（advisor_tools.py）— 4 个 Tool

### 10. collect_preferences

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/advisor_tools.py` |
| **当前状态** | ✅ 可用（状态管理） |
| **升级计划** | 不变 |

**功能**：启动偏好收集对话，返回已收集/待收集的字段。

**参数**：无

**返回值**：
```python
{
    "status": "collecting" | "ready",
    "collected_fields": ["major", "grade"],
    "remaining_fields": ["interests", "preference_type", "target_gpa"],
    "current_profile": {"major": "计算机科学", "grade": "大二"}
}
```

**实现细节**：
- 偏好存储在进程内 `_profiles` 字典中，并按 `session_ctx.current_student()` 分桶；未登录会话使用 `_anon` 桶
- Agent 通过自然语言对话收集字段，调用 `update_profile()` 更新
- 5 个字段：`major`, `grade`, `interests`, `preference_type`, `target_gpa`

**已知问题**：
- 没有持久化，重启后丢失
- 匿名会话共用 `_anon` 桶；正式个人数据只在已认证学号上下文中使用

**Plan-and-Execute 兼容性**：
- Planner 通常在 Step 1 调用此 Tool 检查偏好是否已收集
- 如果 `status == "collecting"`，Planner 应生成"对话收集偏好"的步骤

---

### 11. recommend_courses

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/advisor_tools.py` |
| **当前状态** | ✅ 可用（icourse.club 快照：5667 个课程页 / 44418 条评论 / 632 套培养方案） |
| **升级计划** | 按 `scripts/refresh_course_db.py` 定期刷新评课快照 |

**功能**：根据本轮明确需求、培养方案、已修课程与评课数据推荐课程。登录用户优先使用本人的教务培养方案；个人方案不可用时才按已验证专业/年级降级到本地通用方案。

**参数**：
```python
profile: dict | None = None
major: str | None = None
grade: str | None = None
interests: list[str] | str | None = None
preference_type: "balanced" | "easy_grade" | "learn_hard" | None = None
preference: str | None = None
keywords: list[str] | str | None = None       # 课程范围硬限定
max_results: int = 10
taken_courses: list[str] | None = None         # None 表示未知
current_year_index: int | None = None
current_term: str | None = None
gpa: float | None = None
workload_preference: str | None = None
course_scope: "all" | "required" | "elective" | "general" = "all"
preferred_teachers: list[str] | str | None = None
target_term: str | None = None
personal_tree: dict | list | None = None
```

顶层显式参数覆盖 `profile` 中的旧值。工具已永久移除 `schedule_constraints`；早八、晚课、星期和课表冲突不参与推荐。

**返回值**：
```python
{
    "recommendations": [...],  # 三组拼接，兼容旧调用方
    "groups": {
        "required": [...],     # 培养方案必修
        "elective": [...],     # 方案内选修
        "exploratory": [...]   # 仅由本轮明确兴趣触发，且标注方案外
    },
    "progress": {...} | None,
    "program_context": {
        "matched": True,
        "source": "personal" | "generic" | "unavailable",
        "name": "...",
        "taken_courses_known": True
    },
    "profile_note": {"name": "均衡兼顾", "source": "explicit|gpa_default|default"},
    "limitations": [...],
    "total_candidates": 110,
    "filtered_count": 10
}
```

**推荐算法**：
```
1. 数据边界：个人方案 > 同专业同年级通用方案 > 无方案透明降级。
2. 硬条件：keywords / course_scope；兴趣、工作量、教师、目标学期仅在用户说“只要/必须”时升级为硬过滤。
3. 软排序：兴趣、工作量、给分、挑战性、教师、目标学期默认只影响排序；软偏好零命中时保留硬范围内候选。
4. 缺省画像：本轮明确需求优先；仅在没有明确偏好时按 GPA 选择 easy_grade / balanced / learn_hard。
5. 分组：必修 → 方案内选修 → 方向补充；方向补充只由本轮明确兴趣触发，自动从已修课推测的兴趣不触发。
6. 评分：真实评课均分结合小样本收缩，避免少量满分评论压过稳定课程。
7. 透明性：已修记录未知时不声称“未修缺口”；硬条件零命中不放宽，并写入 limitations。
```

**冲突边界**：
- `recommend_courses` 不检查个人课表，也不接受任何时间偏好参数。
- “推荐几门课并看看是否冲突”只执行普通推荐，并通过 `limitations` 明示尚未检查冲突。
- 已有的独立 `check_course_conflict` 保留，用于用户单独提出课表冲突检查时调用。

**Plan-and-Execute 兼容性**：
- 上层在登录上下文中注入已认证学号对应的 GPA、已修课程和个人方案树。
- 显式推荐动作在确定性路由中优先于 embedding 首分类；仅含“选修”名词的冲突问句仍走独立冲突检查。

---

### 12. compare_courses

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/advisor_tools.py` |
| **当前状态** | ✅ 可用（icourse.club 真实评课快照） |
| **升级计划** | 随评课快照刷新自动更新 |

**功能**：对比两门课程。

**参数**：
```python
course_a: str  # 第一门课名称
course_b: str  # 第二门课名称
```

**返回值**：
```python
{
    "course_a": {"name": "...", "rating": 8.7, "difficulty": 6.5, ...},
    "course_b": {"name": "...", "rating": 8.5, "difficulty": 7.2, ...},
    "comparison": {
        "winner_rating": "机器学习导论",
        "winner_easy": "算法设计与分析",
        "suggestion": "想学东西选评分高的，想轻松选难度低的"
    }
}
```

**实现**：
```sql
SELECT * FROM course_reviews WHERE course_name LIKE '%{course_a}%'
SELECT * FROM course_reviews WHERE course_name LIKE '%{course_b}%'
```

**已知问题**：
- 模糊匹配可能匹配到错误的课
- `suggestion` 是硬编码的一句话，不够个性化

---

### 13. analyze_teacher

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/advisor_tools.py` |
| **当前状态** | ✅ 可用（2982 位教师、同课多师独立统计） |
| **升级计划** | 随评课快照刷新自动更新 |

**功能**：分析指定教师的评价。

**参数**：
```python
teacher_name: str  # 教师姓名
```

**返回值**：
```python
{
    "teacher": "王教授",
    "courses": ["机器学习导论", "深度学习"],
    "avg_rating": 8.8,
    "teaching_style": "善于结合实例、课件精美",
    "strengths": ["科研能力强", "课程紧跟前沿"],
    "weaknesses": ["实验环境要求高"],
    "review_summary": "...",
    "review_count": 85
}
```

**实现**：
```
1. 先查 teacher_reviews 表
2. 找不到 → 从 course_reviews 聚合（按 teacher 字段）
3. 仍找不到 → 返回 error
```

---

## 模块四：日程管理（schedule_tools.py）— 5 个 Tool

### 14. add_event

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/schedule_tools.py` |
| **当前状态** | ✅ 可用 |
| **升级计划** | 不变 |

**功能**：添加日程事件，自动检测冲突。

**参数**：
```python
student_id: str           # 学号
title: str                # 标题
start_time: str           # ISO格式 "2026-07-29T15:00:00"
end_time: str             # ISO格式
location: str = None      # 地点（可选）
description: str = None   # 备注（可选）
```

**返回值**：
```python
{
    "success": True,
    "event_id": 123,
    "conflicts": [],           # 冲突事件列表
    "has_conflict": False
}
```

**验证逻辑**：
- title 不能为空
- start_time < end_time
- 时间格式必须为 ISO
- 自动检查时间重叠

**Plan-and-Execute 兼容性**：
- 可依赖其他 Step 的结果（如"把考试添加到日程" → `{step_1.exam_date}`）

---

### 15. get_day_view

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/schedule_tools.py` |
| **当前状态** | ✅ 可用 |
| **升级计划** | 不变 |

**功能**：获取指定日期的日程视图。

**参数**：
```python
student_id: str            # 学号
date_str: str = None       # 日期 ISO 格式，默认今天
```

**返回值**：
```python
{
    "date": "2026-07-31",
    "day_of_week": "周五",
    "events": [
        {"title": "...", "type": "course", "start_time": "08:00", "end_time": "09:35", ...}
    ],
    "count": 3
}
```

---

### 16. get_week_view

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/schedule_tools.py` |
| **当前状态** | ✅ 可用 |
| **升级计划** | 不变 |

**功能**：获取本周日程概览。

**参数**：
```python
student_id: str               # 学号
start_date: str = None        # 周起始日期，默认本周一
```

**返回值**：
```python
{
    "week_start": "2026-07-27",
    "week_end": "2026-08-02",
    "daily": {
        "周一": {"event_count": 3, "busy_hours": 4.5},
        "周二": {"event_count": 0, "busy_hours": 0},
        ...
    },
    "total_events": 12,
    "busiest_day": "周三",
    "free_days": ["周二", "周六"]
}
```

---

### 17. check_conflict

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/schedule_tools.py` |
| **当前状态** | ✅ 可用 |
| **升级计划** | 不变 |

**功能**：检查指定时间段是否有冲突。

**参数**：
```python
student_id: str    # 学号
start_time: str    # ISO 格式
end_time: str      # ISO 格式
```

**返回值**：
```python
{
    "has_conflict": True,
    "conflicts": [
        {"event_id": 5, "title": "数学分析B2", "time": "...", "type": "course"}
    ]
}
```

---

### 18. import_schedule

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/schedule_tools.py` |
| **当前状态** | ✅ 可用（依赖 query_schedule） |
| **升级计划** | ★ 随 query_schedule 自动升级 |

**功能**：将课表导入为重复日程事件。

**参数**：
```python
student_id: str  # 学号
```

**返回值**：
```python
{
    "imported_count": 5,
    "courses": ["数学分析B2", "程序设计基础", ...],
    "note": "已导入 5 门课程，时间范围为第1-18周，每周重复"
}
```

**实现**：
1. 调用 `query_schedule` 获取课表
2. 解析 time 字段（"周一3-4节"）→ 映射到具体时间
3. 插入 events 表（`is_recurring=1, source='schedule_import'`）
4. 去重：已导入的不重复插入

**已知问题（P3-3 已修复，2026-08-22）**：
- `base_date` 硬编码已改读 `config.SEMESTER`（env `XIAOWO_SEMESTER_START` 可覆盖；当前 2026-08-31）
- 节次映射已统一走 `utils/course_periods.PERIOD_TIMES`（13 节含晚课 11-13）

---

## 模块五：选课冲突与退补选评估（selection_tools.py）— 2 个 Tool ★ 新增（2026-08-15 H 项）

> 背景：此前选课时间冲突靠 LLM 目测时间字符串，会把周次不重叠的课误判为冲突。
> 本模块提供精确到周次/节次/时钟的冲突检测与学分压力评估。时间解析与冲突判定
> 逻辑在 `utils/schedule_parse.py`（纯函数，可独立单测）。

### 19. check_course_conflict

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/selection_tools.py` |
| **当前状态** | ✅ 可用（H 项新增） |
| **升级计划** | 候选课排课数据接入后支持推荐课程与已选课表间的冲突检测 |

**功能**：检测已选课程之间的节次级时间冲突（精确到周次/节次；周次不重叠不算冲突）。

**参数**：
```python
student_id: str          # 学号（个人数据工具，未登录返回 locked 提示）
course_names: list[str]  # 可选，只检测这些课程；不在已选数据中的列入 missing 如实标注
```

**返回值**：
```python
{
    "student_id": "PB25111691",
    "total": 14,
    "courses": [...],            # 参与检测的课程（含 time/location/credits）
    "conflicts": [               # 冲突对（同对多时段只报一次）
        {"course_a": "课程A", "course_b": "课程B", "day": "周二",
         "a_time": "...", "b_time": "...", "reason": "节次重叠",
         "weeks_unknown": True}  # True=周次未知按重叠保守判定
    ],
    "conflict_count": 0,
    "time_incomplete": ["社会主义发展史"],   # 无排课时间，无法精确判定
    "missing": [],               # course_names 中未匹配到的课程
    "source": "fallback",        # 本地缓存/模拟数据
    "note": "..."
}
```

**判定口径**：星期不同→不冲突；周次双方已知且不交叠→不冲突（周次未知→保守按重叠并标注）；双方有节次号→按节次交集；否则按时钟区间（节次可换算为时钟）；两者皆无→unknown 如实说明。

### 20. evaluate_selection_pressure

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/selection_tools.py` |
| **当前状态** | ✅ 可用（H 项新增） |
| **升级计划** | 加课模拟接入 catalog 排课后可计入真实学分/冲突 |

**功能**：评估当前选课学分压力与时间负荷，支持模拟退课/加课。

**参数**：
```python
student_id: str            # 学号
add_courses: list[str]     # 可选，模拟加课（无排课/学分数据 → adds_pending 如实标注）
drop_courses: list[str]    # 可选，模拟退课（按归一化课程名匹配）
credit_cap: float          # 可选，学分上限参考值，默认 30（以教务系统为准）
```

**返回值**：
```python
{
    "student_id": "...", "credit_cap": 30.0, "source": "fallback",
    "current": {
        "course_count": 14, "total_credits": 31.0, "margin": 1.0, "over_cap": True,
        "conflict_count": 0, "conflicts": [...],
        "time_incomplete": [...],
        "daily": {"周一": {"course_count": 2, "slot_count": 2, "period_count": 4}, ...},
        "busiest_day": "周二"
    },
    "after_add_drop": {...},   # 模拟后统计（无加退时 None）
    "drops_applied": ["力学B"], "drops_missing": [], "adds_pending": ["量子力学"],
    "suggestions": ["当前总学分 31.0 已超过 30.0 学分参考上限..."],
    "note": "学分上限为参考值（默认 30），最终以教务系统为准..."
}
```

### 共享基础设施补充：utils/schedule_parse.py ★ 新增

- `parse_course_time(time_str)` → 时间段列表（day/day_num/weeks/weeks_raw/periods/clock/raw），兼容 jw 格式、爬取备份格式、实验课时钟变体（`第19:00~19:30节`）、分号多段
- `slots_overlap(a, b)` → `("conflict"|"no_conflict"|"unknown", {reason, weeks_unknown})`
- 节次→时钟换算复用 `utils/course_periods.PERIOD_TIMES`

---

## 共享基础设施

### tools/api_client.py ★ 新增

所有 catalog.ustc.edu.cn API 的统一封装层。

```python
class CatalogAPI:
    BASE_URL = "https://catalog.ustc.edu.cn"
    TIMEOUT = 10  # 秒

    def get_timetable(self, date: str) -> list[dict]
    def get_semesters(self) -> list[dict]
    def get_courses(self, semester_id: str) -> list[dict]
    def get_exams(self, semester_id: str) -> list[dict]
    def search_courses(self, keyword: str) -> list[dict]
```

**设计要点**：
- 所有方法返回原始 JSON（由调用方 Tool 做格式转换）
- 网络错误返回 `{"error": "..."}` 而非抛异常
- 内置简单缓存（同一天内不重复请求同一 API）
- 日志记录每次 API 调用

### 内部函数复用关系

```
course_tools._query_grades()     → query_grade + calc_gpa
schedule_tools._check_conflicts → add_event + check_conflict
schedule_tools._query_day_events → get_day_view + get_week_view
advisor_tools._generate_reason   → recommend_courses
```

---

## Tool 与 Plan-and-Execute 的交互规范

### 所有 Tool 的通用规则

1. **返回值必须是 dict**（LangChain Tool 框架要求）
2. **成功时可不包含 `error` 字段**，失败时必须包含
3. **不包含控制流逻辑**（Tool 不决定下一步做什么，由 Planner/Executor 决定）
4. **无副作用查询类 Tool**（search/query/get/find/check）→ 可安全重试
5. **有副作用写入类 Tool**（add_event/import_schedule）→ Executor 需标记"已执行"防止重复

### Planner 可用的 Tool 映射

```python
TOOL_REGISTRY = {
    # FAQ
    "search_faq": search_faq,
    "get_faq_categories": get_faq_categories,
    # Course
    "query_schedule": query_schedule,
    "find_empty_room": find_empty_room,
    "query_grade": query_grade,
    "calc_gpa": calc_gpa,
    "query_exam": query_exam,
    "search_courses": search_courses,        # ★ 新增
    "get_semester_list": get_semester_list,   # ★ 新增
    # Advisor
    "collect_preferences": collect_preferences,
    "recommend_courses": recommend_courses,
    "compare_courses": compare_courses,
    "analyze_teacher": analyze_teacher,
    # Schedule
    "add_event": add_event,
    "get_day_view": get_day_view,
    "get_week_view": get_week_view,
    "check_conflict": check_conflict,
    "import_schedule": import_schedule,
}
```

### Context 自动提取的 fact 列表

| Tool | 提取的 fact key | 类型 | 示例 |
|------|-----------------|------|------|
| calc_gpa | `gpa` | float | 3.26 |
| calc_gpa | `total_credits` | int | 19 |
| query_schedule | `count` | int | 5 |
| query_grade | `count` | int | 6 |
| query_exam | — | — | 不提取（结构复杂） |
| recommend_courses | `filtered_count` | int | 5 |
| find_empty_room | `count` | int | 3 |
| search_faq | `found` | bool | True |
| add_event | `has_conflict` | bool | False |
| check_conflict | `has_conflict` | bool | True |

---

## 各 Tool 的测试用例（最小集）

### 智能问答
| Tool | 测试输入 | 预期输出 |
|------|---------|---------|
| search_faq | "学生证怎么补办" | found=True, top_score>0.3 |
| search_faq | "" (空输入) | found=False, error非空 |
| get_faq_categories | 无参数 | 返回非空列表 |

### 课业助手
| Tool | 测试输入 | 预期输出 |
|------|---------|---------|
| query_schedule | student_id=PB20240001 | count=5 |
| query_schedule | day="周一" | 只返回周一的课 |
| find_empty_room | building="三教", time="今天下午" | count>0 |
| query_grade | student_id=PB20240001 | count=6 |
| query_grade | course_name="数学" | 只返回数学相关 |
| calc_gpa | student_id=PB20240001 | gpa≈3.26 |
| calc_gpa | semester="2025-2026-1" | 只算该学期 |
| query_exam | student_id=PB20240001 | exams非空 |

### 选课顾问
| Tool | 测试输入 | 预期输出 |
|------|---------|---------|
| collect_preferences | 无参数 | status="collecting" |
| recommend_courses | profile={"interests":["人工智能"]} | 推荐含ML相关课 |
| compare_courses | "机器学习导论" vs "算法设计与分析" | comparison非空 |
| analyze_teacher | "王教授" | courses包含ML课 |
| analyze_teacher | "不存在的老师" | error非空 |

### 日程管理
| Tool | 测试输入 | 预期输出 |
|------|---------|---------|
| add_event | title="考试", start/end合法 | success=True |
| add_event | title="" (空标题) | success=False |
| get_day_view | 今天日期 | count>=0 |
| get_week_view | 无参数 | daily有7天 |
| check_conflict | 与已有事件重叠时间 | has_conflict=True |
| import_schedule | student_id=PB20240001 | imported_count=5 |

---

## 2026-08-22 增补（v2.2：工具生态 + 活动数据线）

> 注册表现状：**28 个内置工具 + 1 个随仓库提供的生态自检工具 `eco:echo`**（`agents/tool_registry.py` 合并 `tools/ecosystem/`）。本文上半部分的旧状态说明以本节和各工具最新正文为准。

### 更正与重写说明（覆盖上文对应条目）

| 条目 | 变更 |
|---|---|
| §6 calc_gpa 绩点表 | 上文"科大4.3制对照表：100→4.3, 95→4.0, 90→3.7"**描述过时**。代码 `utils/gpa_calculator.py` 与官方一致：**94~90→4.0(A)，89~85→3.7(A－)**，90 分对应 4.0 |
| §3 query_schedule / §4 find_empty_room | "模拟数据/种子数据"状态描述过时：主路径为 jw API/catalog API 实时数据 + SQLite 降级；假数据 fallback 已删除（P1-2 治理），不可用时如实提示 |
| §18 import_schedule | **已重写（2026-08-22）**：开学日期读 `config.SEMESTER`；节次换算走 `utils/schedule_parse` + 官方 13 节次表（含晚课 11-13 节）；无"第"前缀写法自动补齐；解析失败不猜时间，`time_unparsed` 如实列出 |
| query_daily_schedule（新增说明） | 降级分支已重写：统一解析器按星期取时段，输出精确时钟/节次/周次（`weeks` 字段）；同课多时段逐段返回 |
| §10 collect_preferences | 已按当前学号分桶隔离；仍为进程内状态、不持久化。活动域画像由 `services/activity_profile.py` 独立承担 |

### 27. render_link（tools/link_tools.py）

| 项目 | 内容 |
|---|---|
| 功能 | 按场景给出校园官方系统/平台跳转入口（强操作类诉求：选退课/评教/缴费等） |
| 参数 | `scene: str`（场景描述，如"退课""交学费""评教"） |
| 返回 | `{"found": true, "name", "url", "description", "category", "matched_keywords", "source": "官方"}`；无匹配 `found=false` + 提示禁止编造 URL |
| 数据源 | `config/links.yaml`（19 条已核实官方链接，六分类 + 场景关键词；8 个高频入口另带 `featured/priority`；与校园服务页共用） |
| 约束 | THINK 规则 21：URL 只能来自本工具返回或知识库来源；找不到入口如实说明 |

### 28. query_activities（tools/activity_tools.py）

| 项目 | 内容 |
|---|---|
| 功能 | 查询青春科大（第二课堂）当前可报名活动（实时，10 分钟 TTL 缓存） |
| 参数 | `keyword`（匹配名称/简介/主办方）、`category`、`time_window`（"即将截止"/"周末"/"本周"）、`limit`、`student_id`（自动注入） |
| 返回 | `{"count", "total_enrolment", "activities": [{name/organizer/category/start/end/apply_start/apply_end/place/campus/contact/form/people_num/service_hour/description}], "fetched_at", "source"}` |
| 数据源 | `services/young_client.py`（报名中列表）；**token 失效自动回退快照**（source 标"本地缓存"）；展示集缺地点/联系人时经详情接口 `fetch_item_detail`（queryItemById）兜底补全（最多 4 条/次，0.6s 节流，进程级缓存） |
| 副作用 | 返回项计入偏好画像 asked 流水（最多 3 条/次） |
| 决策 | THINK 规则 22 + intents「活动推荐」意图；报名入口走规则 21 |

### 培养方案工具来源约定（program_tools.py）

`get_my_program`、`get_program_progress` 和 `plan_semester` 统一使用以下来源语义：

| `source` | 含义 | UI 要求 |
|---|---|---|
| `personal` | 当前认证用户的教务个人培养方案树 | 显示“教务系统个人培养方案” |
| `generic` | 个人方案不可用，按当前用户已验证的专业和年级命中本地通用方案 | 醒目标注“专业通用参考，不是个人培养方案” |
| `unavailable` | 个人方案和对应通用方案均不可用 | 明确报错，不猜专业或年级 |

- 三个工具均返回 `personal`、`source` 和 `fallback_from_personal`；个人方案树通过 `personal_tree` 参数传入。
- CAS 档案学号必须与认证学号一致；专业、年级只从该用户的 `student-info` 或成绩档案提取，年份统一为 `YYYY级`。
- 登录会话缺专业/年级时不读取匿名预览控件；个人方案树注入、CAS 客户端和缓存均按用户隔离。

### 生态工具协议 v1（tools/ecosystem/）

- 加载器扫描 `*.spec.yaml` + 同名 `.py`（`run(params, ctx) -> dict`）；`eco:` 前缀强校验、9 必填字段（name/display_name/provider/description/version/permission/params_schema/result_schema/source_hint）、坏 Spec 拒载不炸、进程级缓存、ctx 注入学号；
- `_TOOL_LIST` 动态纳入（含参数必填/可选提示）；摘要强制署名"第三方工具 · XX 提供，仅供参考"（THINK 规则 20 + COMPOSE 署名保留）；
- 投稿指南见 `tools/ecosystem/README.md`；自检 `scripts/verify_ecosystem.py`。

### 配套服务（非注册工具）

- **`services/young_client.py`（13 方法）**：fetch_enrolment_activities / fetch_end_activities / fetch_item_detail（`/mobile/item/queryItemById`，142 字段，含 placeInfo/xq/linkMan/tel/formName，2026-09-02 实测）/ fetch_my_profile（五维成绩+学时+社团）/ fetch_my_labels（兴趣标签）/ fetch_my_favorites / fetch_my_followed_depts / fetch_tobe_involved（type 必传）；AES-128-CBC 协议，token ~7 天。**公开性分类（2026-09-02 实测）：门户 young.ustc.edu.cn/main.htm 及栏目页公开可浏览；全部数据接口（/mobile/*、/item/*）无 token 一律 500 Token失效，需 X-Access-Token**。
- **`services/activity_profile.py`**：画像（平台标签冷启动 + 模块学时 d德z智t体m美l劳 + 行为权重 90 天窗口）；`personal_score` 三路命中（行为/标签词/均衡补短板，缺口≥20% 计分封顶 0.85，最高模块不加持）。
- **`services/activity_recommender.py`**：四因子 urgency0.30/freetime0.25/personal0.30/hotness0.15（无画像退回三因子）+ MMR；FreeTimeMatcher 已改 schedule_parse 解析。
- **`scripts/crawl_young.py`**：个人快照（`scripts/data/young_personal/young_snapshot.json`）。

## 2026-08-27 增补（v3.0：Web API、联网证据与审核发布）

> 本节描述 `xiaowo_web/` 的公开契约。它不会增加 LangGraph 注册 Tool 数量，也不得改变上文已经确认的课程推荐、独立冲突检查和个人身份边界。完整产品规格见 `docs/小蜗_Web应用与联网RAG技术规格.md`。

### Web 运行模式

- `XIAOWO_AUTH_MODE` 只能为 `anonymous`、`demo`、`cas`，模式互斥。
- demo 身份固定为 `PB25111691 / 测试 / 计算机科学与技术 / 2025级`；demo 管理员只操作 `namespace=demo`，不能发布 production。
- HTTP production 只允许 anonymous，个人学业与审核 API 固定拒绝访问。
- CAS 模式只在精确可信的 HTTPS `XIAOWO_PUBLIC_ORIGIN` 下启用；专业、年级、成绩、课表和培养方案只取当前认证学号的数据。

### API 与 SSE 契约

所有接口使用 `/api/v1`。稳定表面包括：

| 模块 | 端点 |
|---|---|
| 系统 | `GET /health/live`、`GET /health/ready`、`GET /config/public` |
| 认证 | `GET /auth/session`、`POST /auth/demo`、CAS login/callback、`POST /auth/logout` |
| 历史 | `GET/POST /conversations`、删除单条、删除全部 |
| 回答 | `POST /chat/runs`、`GET /chat/runs/{id}/events`、`POST /chat/runs/{id}/cancel` |
| 学业 | `GET /academic/overview`、`/program`、`/courses`、`/schedule` |
| 校园 | `GET /campus/services`、`GET /campus/activities`、`GET /campus/tools`；登录用户另有工具申请、我的申请、通知与已读接口 |
| 反馈 | `POST /answers/{id}/feedback` |
| 管理 | `/admin` 下的校园工具申请/目录/审计，以及知识审核条目、版本、逐块批准、拒绝、撤回、复抓、发布重试、generation、来源规则建议 |

SSE 只发送 `run.created`、固定枚举的 `stage.changed`、`source.found`、compose 阶段的增量正文 `answer.delta`（小段拼接，约 16 字一批）、完整句子/结构块形式的 `answer.segment`、`answer.completed`、`run.cancelled` 或稳定错误码。禁止发送思维链、提示词、工具选择理由、堆栈或凭证。`answer.segment`/`answer.completed` 为替换语义（最终段覆盖 `answer.delta` 已拼出的内容）；生成超时但已有流式正文时以 `answer.completed`（`terminal_reason=GENERATION_TIMEOUT_PARTIAL`、`truncated=true`）部分收尾，不整体失败。事件 ID 在单个 run 内单调递增，并按创建它的 principal 隔离。事件提交 SQLite 后通过进程内通知立即唤醒 SSE；每 1 秒读取 SQLite 作为多进程和漏通知兜底，不再以 0.1 秒固定轮询。

### 本地 QA 适配

- `LegacyQaRunner` 通过有界执行器复用 `agents/qa/graph.py::run_qa`，不把同步 LangGraph 工作阻塞在 FastAPI 事件循环。
- 工具结果、来源、限制和已有课程卡片转换为公开 SSE 事件；转换不得篡改数值或伪造来源。

**2026-09-05 增补（事件面扩展）**：
- **`data.table`**：结构化数据卡（成绩/课表/考试/选课/活动/空教室/培养方案/日周视图/课程搜索 12 类工具结果）——**工具完成即推**（先于正文，前端先行渲染）；内容 `{title, columns, rows, source_tool}`；前端按 `title|tool|行数` 去重。
- **`stage.changed(action=…)`**：动作播报（"已理解问题：意图「X」/已确定并行查询：A+B/✅ A 已获取结果/信息已齐"）——匿名（不含学号/成绩/规则）；per-run 经 QaState 传递，并发 run 不串线。
- **占位段**：run 开始即推 `answer.segment`（"小蜗正在为你整理答案…"），`answer.completed` 时前端以最终 claims 文本替换。
- **`/api/v1/campus/activities`**：新增 `time_window`（含 **今日**——北京时区锚定）与登录态注入 `student_id`（推荐引擎个性化）；活动条目含 `reason`（推荐理由）。
- `ApprovedKnowledgeRetriever` 只读取当前 principal 命名空间在 `review.db` 标记为 active 且完整性有效的 generation；demo、anonymous/CAS 索引严格隔离。
- 推荐侧永久不接收 `schedule_constraints`、`force_calls` 或 `pending_force_calls`；复合“推荐且不冲突”仍只推荐并提示未查课表，独立 `check_course_conflict` 保留。

### 联网证据保底

联网门控在本地 `found=false`、权威证据低于阈值、问题明确要求最新信息或用户本轮选择“联网”时触发；用户选择“本地”时永久禁止该轮联网。

1. `SearchPrivacyGuard` 先确定性清除或拒绝外发学号、姓名、成绩、课表、培养方案、画像、CAS 信息和私密上下文。
2. `SearxngClient` 在 4 秒预算内搜索；科大问题同时走审核白名单通道和全网通道。（2026-09-03 起搜索源按 `XIAOWO_SEARCH_PROVIDER` 用博查（bocha）或 SearXNG；校内事务问题自动注入 `site:ustc.edu.cn` 业务词查询，命中后经本地 bge-reranker 语义精排取 Top 3——详见部署记录 §7/§9。）
3. `Crawl4AiClient` 只调用私有 adapter，Top 3 并行抓取；URL、DNS 和每跳重定向均执行 SSRF 防护与 robots/限速约束。
4. `SourceTrustStore` 按精确 host/path 白名单分级。未知 `*.ustc.edu.cn` 只能获得 `ustc_domain` 标签，不能自动成为 `official_primary`。
5. 一个有效官方一手来源，或两个相互独立且一致的可靠来源，才能支撑确定结论。证据冲突时单列“信息存在分歧”；不足时固定说明“暂未找到足够可靠的联网证据”并展示不足来源。
6. `StructuredClaimExtractor` 默认复用 `LLM_MODEL`，也可由 `XIAOWO_EVIDENCE_EXTRACTOR_MODEL` 独立指定。联网 readiness 必须先通过合成公开文本的结构化输出与逐字引用探针；未配置、超时或格式不合格时保持 fail-closed，并返回可诊断的证据不足原因。

整个 run 从创建起计算 20 秒硬上限：搜索 4 秒、证据 12 秒、生成到第 18 秒，最后 2 秒用于确定性收尾。达到证据门槛立即取消低优先级抓取。

### 异步反哺与审核

- 当前回答完成后只把实际引用过的公开快照、片段、脱敏主题、证据关系和抓取元数据写入持久队列；不保存原始问题、账号、个人资料、私密上下文或完整回答。
- `python -m xiaowo_web.worker` 独立执行清洗、近重复检查、复抓和发布；worker 停止不影响聊天完成。
- 审核者查看不可变原文、模型稿、人工版本、diff 和分块；每块必须明确为 `approved` 或 `rejected`，不能在仍有 `pending` 时发布，且至少一块为 `approved`。模型不得自动批准、认定官方或解决冲突。
- 默认有效期：公告 7 天、动态办事信息 30 天、政策 90 天、稳定通识 180 天；到期退出有效检索但保留历史。
- 来源白名单建议只导出 Git diff；实际等级变更必须修改 `config/source_trust.yaml`，经测试和 Git 审查后部署。
- `approved` 是实际持久状态；发布 worker 领取关联任务后才转为 `pending_publish`。发布任务记录具体条目，失败只回写本任务目标。完成任务保留 7 天、死信保留 90 天，入队内容哈希墓碑和审核审计长期保留。

### 校园工具申请与审核

- 官方 `config/links.yaml` 与社区校园工具严格分开；社区工具不得标为官方配置，也不进入 LangGraph 工具注册表。
- 匿名可读取当前 production 已上架目录；Demo 读取 demo 目录。只有 Demo/CAS 登录身份可提交，申请人学号由服务端 principal 绑定。
- 申请字段为名称、公开 HTTPS URL、受控分类和可选说明。URL 校验拒绝 userinfo、敏感凭证参数、回环/私网/云元数据目标；展示外链使用 `noopener noreferrer`。
- `campus_tool_applications -> campus_tools -> user_notifications` 与 `campus_tool_audit` 使用审核库 SQLite 事务。批准同时上架并通知；驳回/下架必须填写原因并通知。
- 管理 API 复用 `require_reviewer(_mutation)`、CSRF 与 Origin 校验；namespace 只从 principal 推导。写操作使用 `expected_version` 乐观锁和 `X-Request-ID` 幂等边界，重复编号不得跨对象复用。
- `/admin` 是独立管理入口，默认工具审核；`/admin/knowledge` 为知识审核；旧 `/review` 只作兼容重定向。

### Generation 发布约束

批准内容构建新的不可变 Chroma/BM25 generation。两边写入与校验都成功后，`review.db` 才原子切换 active 指针；部分失败不得改变当前检索结果。worker 领取前合并同 namespace 的 queued 发布任务；Chroma embedding 按“模型身份 + 内容哈希”持久复用，仍完整构建不可变 generation。只保留 active、previous 和 7 天清理窗内的孤儿版本。

回滚 API `POST /api/v1/admin/generations/rollback` 只切换到上一完整 generation。切换前同时验证 manifest、BM25 文件、Chroma collection 名称、generation 元数据、文档数以及 `(document_id, content_hash)` 指纹；任何失败返回 `GENERATION_INTEGRITY_INVALID` 并保持 active 指针不变。

运行与迁移步骤见 `docs/Web部署与数据迁移.md`。

### 2026-09-03 增补（另侧提交 + 本机实测同步）

- **新工具（另侧 7aa21ca 已注册）**：`search_all_lessons`（全校开课检索，客户端关键词过滤；tool count 口径 30，registry 实测 29 键——`_TOOL_LIST` 未同步项另侧待修）；`query_exam` 主源已切换为**教务个人考试安排**（jw `/for-std/exam-arrange/info/{dataId}`，含考场/校区；catalog 公共考试列表降为兜底）；`get_current_teach_week` 教学周校准（CAS 用户，30min TTL）。
- **官方站点直采（本机 2026-09-03）**：`scripts/collect_official_pages.py`（SOURCES 配置驱动，teach 教务 RSS `/category/notice/feed` 起步）+ `deploy/server/official_collect_loop.sh`（每日 05:45）→ 经"采集→审核→发布"管线进发布库（级别 official_primary）；闭环验证：2 条"一〇七杯"通知 active、检索命中。
- **发布知识检索说明**：`ApprovedKnowledgeRetriever` 为 BM25 词法（`_BuiltinBM25` + CJK 分词），发布 chroma 向量仅作产物备存（当前 gen-3CpW 60 条已对齐）——未启用语义混合检索。
