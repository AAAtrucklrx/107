# tab4 — 日程管理 Agent · 详细设计文档

> 本文档是"小蜗"日程管理模块的详细设计。此文档可直接交给 AI Agent 进行代码实现。
> 参考：myshare.md — 精确到交互细节、Tool 定义、禁止清单、边界情况。

---

## 一、模块概述

### 1.1 一句话描述

**整合课表 + 作业截止日期 + 考试安排 + 自定义事件，智能提醒、自动检测冲突的日程管家**。用自然语言添加日程，一句话查看"今天/这周要做什么"。

### 1.2 用户场景

| 场景 | 用户输入示例 | 期望输出 |
|------|-------------|---------|
| 查看今天日程 | "今天有什么事？" | 今日课表 + 待办事项 + 考试/作业提醒 |
| 添加日程 | "下周三下午3点有个组会，提醒我一下" | 日程已添加 + 冲突检测结果 |
| 查看本周 | "这周忙不忙？" | 本周日程概览（按天列出） |
| 冲突检测 | "周四下午有时间开个会吗？" | 该时段是否有空 |
| 导入课表 | "把我这学期的课加进日程" | 课表数据自动导入为重复事件 |

### 1.3 与其他模块的联动

- **课业助手** → 日程管理：课表数据可以一键导入为日程
- **日程管理** ← 独立模块：也可以不依赖课业助手，手动输入所有日程
- 第一版实现手动输入 + 从课业助手导入课表的混合模式

---

## 二、功能清单

| 编号 | 功能 | 描述 | 优先级 |
|------|------|------|--------|
| F1 | 添加日程 | 自然语言添加日程："明天下午3点开组会，2小时" | P0 |
| F2 | 查看今日日程 | "今天有什么事" → 返回今日所有日程 | P0 |
| F3 | 查看本周日程 | "这周忙不忙" → 返回本周日程概览 | P0 |
| F4 | 冲突检测 | 添加新日程时自动检测时间冲突 | P1 |
| F5 | 日程提醒 | 临近日程时在界面内提醒 | P1 |
| F6 | 导入课表 | 从课业助手导入课表为重复事件 | P1 |
| F7 | 删除/修改日程 | "取消明天下午的组会" → 删除日程 | P2 |
| F8 | 查看指定日期 | "7月25号有什么事" → 返回指定日期日程 | P1 |

### 禁止清单

- ❌ **不做日历同步**：不与 Google Calendar / Apple Calendar / 飞书日历同步（第一版）
- ❌ **不做分享/协作**：不搞"邀请好友加入日程"之类的功能
- ❌ **不做复杂重复规则**：不处理"每月第三个周三"这种复杂重复（只支持"每周重复"）
- ❌ **不做通知推送**：不搞浏览器通知/邮件提醒（只在应用内显示提醒）
- ❌ **不做优先级排序**：不搞四象限/ZTD/GDT 那一套（那就是另一个App了）

---

## 三、数据结构

### 3.1 SQLite 表设计

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,       -- 关联学生（第一版固定为 PB20240001）
    title TEXT NOT NULL,            -- 日程标题
    event_type TEXT NOT NULL,       -- "course" | "exam" | "homework" | "custom"
    start_time TEXT NOT NULL,       -- ISO格式 "2026-07-25T15:00:00"
    end_time TEXT NOT NULL,         -- ISO格式 "2026-07-25T17:00:00"
    location TEXT,                  -- 地点（可选）
    description TEXT,               -- 备注（可选）
    is_recurring INTEGER DEFAULT 0, -- 是否重复（0=否，1=每周重复）
    source TEXT DEFAULT 'manual',   -- "manual" | "schedule_import" | "exam_import"
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 提醒表（每个日程可以有0-多个提醒）
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    remind_at TEXT NOT NULL,        -- ISO格式，提醒时间点
    is_triggered INTEGER DEFAULT 0, -- 是否已触发
    FOREIGN KEY (event_id) REFERENCES events(id)
);
```

### 3.2 日程类型定义

| event_type | 说明 | 来源 | 颜色标识 |
|-----------|------|------|---------|
| `course` | 课程 | 课表导入 | 科大蓝 `#003D7C` |
| `exam` | 考试 | 课业助手/手动 | 红色 `#D32F2F` |
| `homework` | 作业截止 | 手动 | 橙色 `#ED6C02` |
| `custom` | 自定义 | 手动 | 绿色 `#2E7D32` |

---

## 四、对话流程设计

### 4.1 添加日程的标准流程

```
用户："下周三下午3点到5点在三教301开组会"

Agent思考（ReAct）：
  1. Thought: 用户要添加日程，需要解析时间+内容
  2. Thought: "下周三" = 计算当前日期+找到下一个周三
  3. Action: add_event(
       title="组会",
       start_time="2026-07-29T15:00:00",
       end_time="2026-07-29T17:00:00",
       location="三教301"
     )
  4. Observation: 日程已添加，但检测到该时段与"算法设计"课程冲突！
  5. Answer: "好的，已添加「组会」：7月29日(周三) 15:00-17:00，三教301。
     ⚠️ 注意：该时段与你的「算法设计」课程（15:00-16:35，计算机学院楼301）有冲突！"
```

### 4.2 查看日程的标准流程

```
用户："今天有什么事"

Agent思考：
  1. Action: get_day_view(date="2026-07-24")
  2. Observation: 返回今日4个日程
  3. Answer:
     "📅 今天（7月24日 周三）的日程：
     
     08:00-09:35 | 力学 | 五教5101 | 📘课程
     10:00-11:35 | 数学分析B2 | 三教3A101 | 📘课程
     14:00-15:35 | 程序设计 | 计算机学院楼301 | 📘课程
     19:00-21:00 | 机器学习作业截止 | 📝作业
     
     ⏰ 作业今晚截止，别忘了提交！"
```

### 4.3 冲突检测流程

```
添加日程时自动检测：
1. 查询该时间段内是否存在其他日程
2. 如果有重叠 → 返回冲突日程列表
3. Agent 提示用户"注意冲突"，但仍然添加日程（用户自己决定）
4. 不做强制阻止（用户可能有合理理由，比如选择翘课去组会）
```

---

## 五、Tool 定义

### 5.1 `add_event(student_id, title, start_time, end_time, location=None, description=None) -> dict`

```python
@tool
def add_event(
    student_id: str,
    title: str,
    start_time: str,
    end_time: str,
    location: str = None,
    description: str = None
) -> dict:
    """
    添加一个日程事件。自动检测时间冲突。
    
    Args:
        student_id: 学号
        title: 日程标题
        start_time: 开始时间（ISO格式 "2026-07-29T15:00:00"）
        end_time: 结束时间（ISO格式）
        location: 地点（可选）
        description: 备注（可选）
    
    Returns:
        {
            "success": True,
            "event_id": 123,
            "conflicts": [
                {
                    "event_id": 100,
                    "title": "算法设计",
                    "time": "周三15:00-16:35",
                    "type": "course"
                }
            ],
            "has_conflict": True
        }
    
    Raises:
        ValueError: start_time > end_time时
        ValueError: title为空时
    """
```

### 5.2 `get_day_view(student_id, date=None) -> dict`

```python
@tool
def get_day_view(student_id: str, date: str = None) -> dict:
    """
    获取指定日期的日程视图。
    
    Args:
        student_id: 学号
        date: 日期（ISO格式 "2026-07-24"），不指定则默认今天
    
    Returns:
        {
            "date": "2026-07-24",
            "day_of_week": "周三",
            "events": [
                {
                    "id": 1,
                    "title": "力学",
                    "type": "course",
                    "start_time": "08:00",
                    "end_time": "09:35",
                    "location": "五教5101"
                },
                ...
            ],
            "count": 4,
            "upcoming_deadlines": [...]  # 今天及未来的截止日期
        }
    """
```

### 5.3 `get_week_view(student_id, start_date=None) -> dict`

```python
@tool
def get_week_view(student_id: str, start_date: str = None) -> dict:
    """
    获取本周日程概览。
    
    Args:
        student_id: 学号
        start_date: 周起始日期（ISO格式），默认本周一
    
    Returns:
        {
            "week_start": "2026-07-20",
            "week_end": "2026-07-26",
            "daily": {
                "周一": {"event_count": 3, "busy_hours": 5},
                "周二": {"event_count": 2, "busy_hours": 3},
                ...
            },
            "total_events": 15,
            "busiest_day": "周三",
            "free_days": ["周六", "周日"]
        }
    """
```

### 5.4 `check_conflict(student_id, start_time, end_time) -> dict`

```python
@tool
def check_conflict(student_id: str, start_time: str, end_time: str) -> dict:
    """
    检查指定时间段是否有冲突。
    
    Args:
        student_id: 学号
        start_time: 开始时间（ISO格式）
        end_time: 结束时间（ISO格式）
    
    Returns:
        {
            "has_conflict": True,
            "conflicts": [
                {"title": "力学", "time": "08:00-09:35", "type": "course"}
            ]
        }
    """
```

### 5.5 `import_schedule(student_id) -> dict`

```python
@tool
def import_schedule(student_id: str) -> dict:
    """
    从课业助手导入课表数据为重复日程。
    此Tool调用课业助手的query_schedule，将结果转为events表记录。
    
    Args:
        student_id: 学号
    
    Returns:
        {
            "imported_count": 5,
            "courses": ["力学", "数学分析B2", ...],
            "note": "已导入5门课程，时间范围为第1-18周，每周重复"
        }
    """
```

---

## 六、提醒机制设计

### 6.1 提醒规则

| 事件类型 | 默认提醒时间 | 可自定义 |
|---------|------------|---------|
| 课程 | 上课前30分钟 | 否 |
| 考试 | 考前1天 + 考前1小时 | 否 |
| 作业截止 | 截止前1天 + 截止前3小时 | 否 |
| 自定义 | 开始前15分钟 | 是 |

### 6.2 提醒展示方式（仅在应用内）

用户打开页面时，如果有未触发的提醒，在对话区顶部显示提醒横幅：

```
⏰ 提醒：
- 30分钟后有「力学」课（五教5101）
- 明天是「数学分析」期末考试（三教3A101 14:00-16:00）
- 今晚「机器学习作业」截止（23:59）
```

### 6.3 "今天应该被温柔地安排好"

> 参考 myshare.md 时间流模块的设计理念——"像慢慢流动的河水，而不是高压任务管理器"

日程管理的设计哲学：
- 早上打开小蜗，看到今天的日程，感觉是"OK今天有这些事，可以搞定"
- 而不是"天哪今天这么多事压力好大"
- 冲突提醒用"注意"而不是"警告"
- 空闲时段不标灰，保持视觉干净

---

## 七、UI展示规范

### 7.1 今日日程展示格式

```
📅 7月24日 周三

08:00-09:35  📘 力学                        五教5101
10:00-11:35  📘 数学分析B2                  三教3A101
14:00-15:35  📘 程序设计                    计算机学院楼301
──────────────────────────────────────
19:00        📝 机器学习作业Deadline！       

💡 上午较忙（3节连堂），下午只有一节课比较轻松～
```

### 7.2 本周概览展示格式

```
📊 第5周（7/20-7/26）日程概览

周一 ████░░░░  4件事，忙6小时
周二 ███░░░░░  2件事，忙3小时
周三 █████░░░  4件事，忙7小时  ← 最忙
周四 ██░░░░░░  2件事，忙2小时
周五 ███░░░░░  3件事，忙4小时
周六 ░░░░░░░░  无安排 🎉
周日 ░░░░░░░░  无安排 🎉
```

---

## 八、边界情况处理

| 边界情况 | 处理方式 |
|---------|---------|
| 添加过去时间的日程 | "这个时间已经过去了。你是想添加到未来的某天吗？" |
| 开始时间 > 结束时间 | "开始时间不能晚于结束时间，请重新输入。" |
| 自然语言解析时间失败 | "抱歉，我没理解你指的是什么时间。可以明确一点吗？比如'明天下午3点'。" |
| 日程已经完全重叠（同一时段两个事件） | 检测冲突但允许添加，提示"该时段已有安排" |
| 查询的日期无日程 | "这天目前没有安排，享受空闲时间吧～" |
| 跨天事件（如通宵赶DDL 23:00-02:00） | 拆分为两天的事件：23:00-23:59 + 00:00-02:00 |
| 大量日程（>20个/天） | 不折叠，滚动展示，但顶部提示"今天安排比较满，注意休息" |
| 课表导入时发现已存在重复 | 跳过已存在的日程，提示"x门课程已存在，跳过" |

---

## 九、测试用例（8 条必过测试）

| 编号 | 输入 | 期望输出 |
|------|------|---------|
| T1 | "今天有什么事" | 返回今日日程列表 |
| T2 | "这周忙不忙" | 返回本周日程概览 |
| T3 | "下周三下午3点在三教301开组会" | 添加日程 + 自动检测冲突 |
| T4 | "周四下午2点有空吗" | 该时段是否有冲突 |
| T5 | "帮我导入课表" | 课表数据导入为日程 |
| T6 | "下周三下午3点开组会"（与已有课冲突） | 添加成功但提示冲突 |
| T7 | "昨天下午3点开会" | 提示"时间已过去" |
| T8 | ""今天""（仅输入日期词，无语义） | 返回今日日程 |

---

## 十、参考产品

| 产品 | 借鉴点 |
|------|--------|
| **Things 3** | 简洁清爽的日程列表风格 |
| **Google Calendar** | 时间轴视图 + 冲突检测逻辑 |
| **Structured** | 时间线式日程展示，一目了然 |
| **Apple 日历** | 事件颜色分类系统 |
| **Reach and Rich（myshare.md）** | 时间流的设计理念——"像慢慢流动的河水" |

---

*本文档版本：v1.0 | 最后更新：2026年7月24日 | 对应模块：日程管理 Agent*
