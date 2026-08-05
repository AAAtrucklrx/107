# 🐌 小蜗 — Tool 详细规格说明

> 本文档逐一定义每个 Tool 的当前实现、升级方案和 Plan-and-Execute 兼容性。
> 最后更新：2026年7月（2026年8月随 dev-plan 归档，内容仍为当前实现基准）

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
| **当前状态** | ⚠️ 模拟数据（student_courses 表只有种子数据） |
| **升级计划** | ★ B2 — 对接真实课表 API |

**功能**：查询指定学生的课程表。

**参数**：
```python
student_id: str = None  # 学号，默认 DEMO_STUDENT["id"] = "PB20240001"
week: int = None        # 周次（可选）
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
            "time": "周一3-4节;周三1-2节;周五3-4节",
            "location": "三教3A101",
            "semester": "2025-2026-2"
        },
        ...  # 最多5门（种子数据）
    ],
    "count": 5
}
```

**当前实现**：
```sql
SELECT * FROM student_courses WHERE student_id = ? [AND time LIKE '%周一%']
```

**★ B2 升级方案**：
```
真实数据源：CatalogAPI.get_courses(semester_id)
API: /api/teach/lesson/list-for-teach/{semesterId}
流程：
  1. 调用 API 获取本学期全部开课数据
  2. 与学生选课列表交叉匹配（需登录教务系统获取选课列表，暂不可行）
  3. 回退方案：用 student_courses 表（种子数据）+ API 补充教室/时间信息
```

**关键决策点**：
> ⚡ 真实课表需要学生登录教务系统才能获取个人选课列表。
> 当前 catalog API 只能获取"全校开课信息"，无法知道某学生选了哪些课。
> **建议**：B2 阶段保持 student_courses 表作为课表来源，用 API 补充教室占用等辅助信息。
> 后续如需真实选课列表，再对接教务系统认证。

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
科大4.3制对照表：100→4.3, 95→4.0, 90→3.7, 85→3.3, 82→3.0, ...
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
- 偏好存储在模块级 `_current_profile` 字典中（进程内单例）
- Agent 通过自然语言对话收集字段，调用 `update_profile()` 更新
- 5 个字段：`major`, `grade`, `interests`, `preference_type`, `target_gpa`

**已知问题**：
- 模块级全局变量，多用户场景下数据会互相覆盖
- 没有持久化，重启后丢失

**Plan-and-Execute 兼容性**：
- Planner 通常在 Step 1 调用此 Tool 检查偏好是否已收集
- 如果 `status == "collecting"`，Planner 应生成"对话收集偏好"的步骤

---

### 11. recommend_courses

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/advisor_tools.py` |
| **当前状态** | ✅ 可用（基于种子数据 8 门课） |
| **升级计划** | ★ B6 — 扩充评课数据后自动提升推荐质量 |

**功能**：根据用户偏好推荐课程。

**参数**：
```python
profile: dict  # {
    "major": "计算机科学",
    "grade": "大二",
    "interests": ["人工智能"],
    "preference_type": "balanced" | "easy_grade" | "learn_hard",
    "target_gpa": 3.5,
    "max_results": 5
}
```

**返回值**：
```python
{
    "recommendations": [
        {
            "course_name": "机器学习导论",
            "teacher": "王教授",
            "rating": 8.7,
            "difficulty": 6.5,
            "give_score": "给分好",
            "reason": "与你的兴趣（人工智能）高度匹配；评分8.7，口碑很好",
            "review_summary": "...",
            "review_count": 45
        },
        ...  # 最多 max_results 条
    ],
    "total_candidates": 10,
    "filtered_count": 5
}
```

**推荐算法**：
```
score = w_rating × rating + w_give × give_score + w_interest × interest_match

权重配置（按 preference_type）：
  easy_grade: rating=0.3, give_score=0.5, interest=0.2
  learn_hard: rating=0.5, give_score=0.1, interest=0.4
  balanced:   rating=0.4, give_score=0.3, interest=0.3
```

**★ B6 升级方案**：
- 爬虫写入 `course_reviews` 表 → 数据量从 10 → 50+
- 推荐算法不变，数据更多 = 结果更好
- 可选增强：加入"已修课程排除"逻辑（依赖 query_schedule 结果）

**Plan-and-Execute 兼容性**：
- 常依赖 Step N 的 `calc_gpa` 结果（`target_gpa` 可动态计算）
- 占位符示例：`{step_1.gpa}` 作为推荐阈值

---

### 12. compare_courses

| 项目 | 内容 |
|------|------|
| **所属模块** | `tools/advisor_tools.py` |
| **当前状态** | ✅ 可用（基于种子数据） |
| **升级计划** | ★ B6 — 数据扩充后自动升级 |

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
| **当前状态** | ✅ 可用（种子数据 3 位教师） |
| **升级计划** | ★ B6 — 爬虫数据扩充 |

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

**已知问题**：
- `base_date` 硬编码为 `2026-02-23`（假设开学日期）
- `sections_map` 只支持4种节次（1-2, 3-4, 5-6, 7-8），不包含晚课

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
