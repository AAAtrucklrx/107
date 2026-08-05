# 🐌 小蜗 — 开发计划与标准 (v2.0)

> 本文档是小蜗项目下一阶段的开发规范。
> 所有架构设计、代码实现、数据对接均以本文档为基准。
> 最后更新：2026年7月

---

## 一、核心概念定义

### 1.1 Agent 模式术语表

| 术语 | 定义 | 在小蜗中的对应 |
|------|------|---------------|
| **ReAct** | Reasoning + Acting 循环：Think → Act → Observe → 决定下一步 | 每个 Step 内部的执行模式 |
| **Plan-and-Execute** | 先制定完整计划，再逐步执行，每步可调整后续计划 | 全局 Planner Agent 架构 |
| **Planner** | 接收用户输入 + 对话历史，生成多步执行计划 | `agents/planner.py` |
| **Executor** | 逐步执行 Planner 的 Steps，管理中间上下文 | `agents/executor.py` |
| **Step** | 计划中的一个执行单元：包含目标描述 + 要调用的 Tool + 预期输出 | `PlanStep` 数据类 |
| **Context** | 跨 Step 共享的中间结果容器 | `agents/context.py` |
| **Router** | 快速判断查询复杂度：简单的直接路由，复杂的交给 Planner | `agents/router.py`（升级） |
| **Tool** | Agent 可调用的原子操作（查数据库、调API、计算等） | `tools/*.py` 中的 @tool 函数 |

### 1.2 架构演进：从 ReAct 到 Plan-and-Execute

```
v1.0（当前）— 简单 ReAct
━━━━━━━━━━━━━━━━━━━━━━
用户 → Router → 子Agent → 调1个Tool → LLM生成回答
                    ↑ 单步，无反思，无跨Agent协作


v2.0（目标）— Plan-and-Execute
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用户 → Router（判断复杂度）
       ├─ 简单查询 → 子Agent（单Tool，保持现有逻辑）
       │
       └─ 复杂查询 → Planner Agent
            │  生成执行计划：[Step1, Step2, Step3...]
            │
            ├─ Step 1: Think → Act(Tool) → Observe → 存入 Context
            ├─ Step 2: Think(读取Context) → Act(Tool) → Observe → 存入 Context
            ├─ Step 3: Think(读取Context) → Act(Tool) → Observe → 存入 Context
            │  ...
            └─ Final: 整合 Context 中所有结果 → 生成完整回答
```

### 1.3 查询分类标准

Router 需要判断查询属于哪种类型：

| 类型 | 特征 | 处理方式 | 示例 |
|------|------|---------|------|
| **简单查询** | 单一意图，一个Tool可完成 | 直接路由到子Agent | "帮我算GPA" |
| **组合查询** | 多意图，需多个Tool串联 | Planner生成计划 | "我GPA多少，推荐适合我的课" |
| **分析查询** | 需要分析+推理+综合 | Planner + 深度推理 | "帮我分析选课策略" |
| **闲聊/引导** | 非功能性输入 | 直接FAQ兜底 | "哈哈" "你是谁" |

---

## 二、架构设计

### 2.1 文件结构（新增/变更标注 ★）

```
小蜗/
├── app.py                     # 主入口（★ 适配新架构）
├── config.py
│
├── agents/
│   ├── router.py              # ★ 升级：增加复杂度判断
│   ├── planner.py             # ★ 新增：全局 Planner Agent
│   ├── executor.py            # ★ 新增：Step 执行器
│   ├── context.py             # ★ 新增：跨Step上下文管理器
│   ├── factory.py             # Agent 工厂（已有）
│   ├── faq_agent.py           # 不变
│   ├── course_agent.py        # 不变
│   ├── advisor_agent.py       # 不变
│   └── schedule_agent.py      # 不变
│
├── tools/
│   ├── faq_tools.py           # 不变
│   ├── course_tools.py        # ★ 升级：对接真实API
│   ├── advisor_tools.py       # ★ 升级：对接评课爬虫数据
│   ├── schedule_tools.py      # 不变
│   └── api_client.py          # ★ 新增：catalog.ustc.edu.cn API封装
│
├── scrapers/
│   └── icourse.py             # ★ 新增/完善：评课社区爬虫
│
├── knowledge/                 # 不变
├── database/                  # 不变
├── services/                  # 不变
├── ui/                        # 不变（后续优化）
└── utils/                     # 不变
```

### 2.2 Planner Agent 设计

```python
# agents/planner.py — 核心接口（伪代码）

from dataclasses import dataclass, field

@dataclass
class PlanStep:
    """计划中的一个执行步骤"""
    step_id: int               # 步骤编号
    description: str           # 步骤目标描述（给LLM看的）
    tool_name: str | None      # 要调用的Tool名（可选，纯推理步骤可为None）
    tool_args: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)
    status: str = "pending"    # pending | running | done | failed

@dataclass
class Plan:
    """执行计划"""
    steps: list[PlanStep]
    original_query: str        # 用户原始输入
    reasoning: str             # Planner的推理过程

PLANNER_SYSTEM_PROMPT = """你是小蜗的任务规划器。
你的职责是分析用户的复杂查询，拆解为可执行的步骤计划。

规则：
1. 每个步骤只调用一个Tool，或进行纯推理分析
2. 步骤之间可以通过 depends_on 建立依赖关系
3. 如果某步骤依赖前序步骤的结果，在 tool_args 中用 {step_N.field} 占位
4. 计划步骤数不超过5步（避免过度拆解）
5. 如果用户查询其实很简单，返回单步计划即可

输出格式（严格JSON）：
{
    "reasoning": "分析用户意图和所需信息的过程",
    "steps": [
        {
            "step_id": 1,
            "description": "查询用户当前GPA",
            "tool_name": "calc_gpa",
            "tool_args": {"student_id": "PB20240001"},
            "depends_on": []
        },
        {
            "step_id": 2,
            "description": "根据GPA推荐适合的课程",
            "tool_name": "recommend_courses",
            "tool_args": {"profile": {"target_gpa": "{step_1.gpa}"}},
            "depends_on": [1]
        }
    ]
}
"""
```

### 2.3 Executor 设计

```python
# agents/executor.py — 核心接口（伪代码）

class Executor:
    """逐步执行Plan，管理ReAct循环"""

    def __init__(self):
        self.tool_registry = {}  # tool_name → tool函数 的映射

    def execute(self, plan: Plan, context: Context) -> str:
        """
        执行流程：
        for each step in plan.steps:
            1. Think: 读取context，分析当前状态
            2. Act: 调用step指定的Tool（替换占位符为实际值）
            3. Observe: 将Tool返回结果存入context
            4. Decide: 判断是否需要调整后续plan
        return 整合所有step结果，调用LLM生成最终回答
        """
        for step in plan.steps:
            # 解析依赖占位符
            resolved_args = self._resolve_placeholders(step.tool_args, context)

            # 执行Tool
            result = self._call_tool(step.tool_name, resolved_args)
            step.status = "done" if "error" not in result else "failed"

            # 存入Context
            context.add_step_result(step.step_id, result)

        # 整合结果，生成回答
        return self._synthesize(plan, context)

    def _resolve_placeholders(self, args: dict, context: Context) -> dict:
        """将 {step_1.gpa} 替换为实际值"""
        ...

    def _call_tool(self, tool_name: str, args: dict) -> dict:
        """通过名称查找并调用Tool"""
        ...

    def _synthesize(self, plan: Plan, context: Context) -> str:
        """调用LLM，根据plan和所有step结果生成最终回答"""
        ...
```

### 2.4 Context 管理器

```python
# agents/context.py

class Context:
    """跨Step共享的上下文容器"""

    def __init__(self):
        # 会话级（跨多次Plan执行保持）
        self.chat_history: list[dict] = []
        self.user_profile: dict = {}

        # 计划级（每次Plan执行时重置）
        self.step_results: dict[int, dict] = {}
        self.intermediate_facts: dict = {}

    def add_step_result(self, step_id: int, result: dict):
        """存储某Step的Tool返回结果"""
        self.step_results[step_id] = result
        # 自动提取关键事实
        self._extract_facts(step_id, result)

    def get_step_result(self, step_id: int) -> dict | None:
        """获取某Step的结果"""
        return self.step_results.get(step_id)

    def _extract_facts(self, step_id: int, result: dict):
        """从Tool结果中提取关键事实，供后续Step引用"""
        # 例如：calc_gpa 返回 {"gpa": 3.53} → 提取 gpa=3.53
        for key in ["gpa", "count", "total_credits", "has_conflict"]:
            if key in result:
                self.intermediate_facts[f"step_{step_id}.{key}"] = result[key]

    def reset_plan_context(self):
        """新Plan开始前重置计划级数据"""
        self.step_results.clear()
        self.intermediate_facts.clear()
```

### 2.5 Router 升级设计

```python
# agents/router.py — 升级后的路由逻辑

def route_query(user_input: str, selected_module: str = None) -> dict:
    """
    返回值新增 complexity 字段：
    {
        "agent": "faq" | "course" | "advisor" | "schedule" | "planner",
        "complexity": "simple" | "complex",
        "reason": "...",
        "rewritten_query": "..."
    }
    """
    # 1. 手动模块选择 → simple
    # 2. 关键词匹配 → simple
    # 3. 多意图检测 → 包含多个不同模块的关键词 → complex → planner
    # 4. 推理型关键词（分析/规划/安排/对比+推荐）→ complex → planner
    # 5. LLM判断（兜底）
    ...
```

---

## 三、Tool 升级策略

### 3.1 原则

- **能升级就升级**：有真实API的Tool，替换模拟逻辑为真实调用
- **不能升级就保留**：没有真实数据源的Tool，保持当前模拟逻辑
- **新增 Tool 最小化**：只在现有Tool无法满足需求时才新增
- **所有Tool必须兼容Plan-and-Execute**：返回结构化dict，包含 `error` 字段

### 3.2 Tool 升级清单

| Tool | 当前状态 | 升级计划 | 数据来源 |
|------|---------|---------|---------|
| `search_faq` | ✅ 可用 | 不变 | 本地Markdown知识库 |
| `get_faq_categories` | ✅ 可用 | 不变 | 同上 |
| `query_schedule` | 模拟数据 | ★ 对接真实课表 | `/api/teach/lesson/list-for-teach/{semesterId}` |
| `find_empty_room` | 模拟数据 | ★ 对接教室占用 | `/api/teach/timetable-public-all/{date}` |
| `query_grade` | 模拟数据 | 暂保留（需登录） | — |
| `calc_gpa` | 模拟数据 | 暂保留（依赖query_grade） | — |
| `query_exam` | 模拟数据 | ★ 对接考试API | `/api/teach/exam/list/{semesterId}` |
| `collect_preferences` | ✅ 可用 | 不变 | 对话收集 |
| `recommend_courses` | ✅ 可用 | ★ 扩充评课数据 | 爬虫 icourse.club |
| `compare_courses` | ✅ 可用 | ★ 扩充评课数据 | 同上 |
| `analyze_teacher` | ✅ 可用 | ★ 扩充教师数据 | 同上 |
| `add_event` | ✅ 可用 | 不变 | SQLite |
| `get_day_view` | ✅ 可用 | 不变 | SQLite |
| `get_week_view` | ✅ 可用 | 不变 | SQLite |
| `check_conflict` | ✅ 可用 | 不变 | SQLite |
| `import_schedule` | ✅ 可用 | ★ 课表对接后自动升级 | 随query_schedule |

### 3.3 新增 Tool（最小化）

| Tool | 用途 | 模块 |
|------|------|------|
| `search_courses` | 在真实课程库中搜索课程 | course_tools |
| `get_semester_list` | 获取可用学期列表 | course_tools |

### 3.4 API Client 封装

```python
# tools/api_client.py — catalog.ustc.edu.cn 统一封装

import requests

class CatalogAPI:
    BASE_URL = "https://catalog.ustc.edu.cn"
    HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    def get_timetable(self, date: str) -> list[dict]:
        """获取某天全校教室占用 /api/teach/timetable-public-all/{date}
        返回字段：buildingCode, classroomName, courseName, teachers, start, end
        注意：仅返回当天有排课的教室，暑假期间数据稀疏
        """
        ...

    def get_semesters(self) -> list[dict]:
        """获取学期列表 /api/teach/semester/list"""
        ...

    def get_courses(self, semester_id: str) -> list[dict]:
        """获取某学期全部开课 /api/teach/lesson/list-for-teach/{semesterId}"""
        ...

    def get_exams(self, semester_id: str) -> list[dict]:
        """获取考试安排 /api/teach/exam/list/{semesterId}"""
        ...

    def search_courses(self, keyword: str) -> list[dict]:
        """课程搜索 /api/teach/course/search (POST)"""
        ...
```

---

## 四、开发阶段

### 阶段 A：架构升级（Plan-and-Execute）

**目标**：实现全局 Planner，支持复杂查询的多步推理。

| 步骤 | 任务 | 产出 | 验证标准 |
|------|------|------|---------|
| A1 | 实现 `context.py` | Context 类 | 单元测试：存取fact、获取step结果 |
| A2 | 实现 `planner.py` | Planner Agent + PlanStep/Plan | 输入复杂查询，输出合理Plan |
| A3 | 实现 `executor.py` | Executor 类 | 给定Plan+Context正确执行 |
| A4 | 升级 `router.py` | 增加 complexity 判断 | 10条测试分类准确率>80% |
| A5 | 集成到 `app.py` | 完整链路跑通 | "帮我查GPA然后推荐课程" |
| A6 | 编写 Planner Prompt | system prompt 调优 | 计划生成质量人工评审 |

**不改动**：现有4个子Agent、16个Tool、数据库、知识库。

### 阶段 B：真实数据对接

**目标**：替换模拟数据为真实API，无需登录的优先。

| 步骤 | 任务 | 产出 | 验证标准 |
|------|------|------|---------|
| B1 | 实现 `api_client.py` | CatalogAPI 封装 | 5个端点均能返回数据 |
| B2 | 升级 `query_schedule` | 对接课表API | 返回真实课程列表 |
| B3 | 升级 `find_empty_room` | 对接教室占用API | 返回真实空教室 |
| B4 | 升级 `query_exam` | 对接考试API | 返回真实考试信息 |
| B5 | 实现评课爬虫 | icourse.py | 至少50门课程评价 |
| B6 | 升级推荐Tool | 使用爬虫数据 | 推荐与评课评分一致 |

### 阶段 C：体验打磨

| 步骤 | 任务 | 产出 |
|------|------|------|
| C1 | 对话历史持久化 | 刷新页面后对话不丢失 |
| C2 | 流式输出 | 打字机效果 |
| C3 | 错误恢复 | Tool失败自动重试/降级 |
| C4 | 移动端优化 | 响应式布局改进 |

---

## 五、开发标准

### 5.1 代码规范

| 规则 | 要求 |
|------|------|
| 日志 | 使用 `utils/logger.py` 的 `get_logger()`，禁止 `print()` |
| 依赖注入 | 通过 `ServiceContainer` 获取 db/store，禁止全局变量 |
| Agent创建 | 通过 `agents/factory.py` 的 `build_agent()` |
| 类型标注 | 所有函数参数和返回值必须有 type hint |
| 文档字符串 | 所有 @tool 函数必须有完整 docstring |
| 错误处理 | Tool 返回 dict 必须包含 `error` 字段（成功时可不包含） |
| 路径 | 使用 `Path` 对象，禁止硬编码路径字符串 |

### 5.2 Tool 编写标准模板

```python
@tool
def example_tool(param1: str, param2: int = None) -> dict:
    """
    一句话描述工具做什么。
    详细说明使用场景和限制。

    Args:
        param1: 参数说明
        param2: 可选参数说明

    Returns:
        {
            "result_key": "结果描述",
            "error": "错误信息（成功时不包含此字段）"
        }
    """
    try:
        db = _db()
    except RuntimeError:
        return {"error": "数据库未初始化"}

    # 业务逻辑...
    return {"result_key": value}
```

### 5.3 Agent Prompt 编写标准

- 每条规则编号，便于调试时定位
- 明确列出"禁止做的事"
- 包含输出格式示例
- 注明学生ID默认值
- 使用中文编写

### 5.4 测试标准

| 类型 | 方法 | 通过标准 |
|------|------|---------|
| 单元测试 | 直接调用Tool函数 | 返回值结构正确 |
| 集成测试 | 通过Agent处理自然语言 | 正确路由+正确Tool调用 |
| 端到端测试 | Streamlit界面操作 | 5分钟无bug演示 |
| 边界测试 | 空输入/非法输入/超长输入 | 友好错误提示，不崩溃 |

### 5.5 Git 提交规范

```
feat: 新增 xxx 功能
fix: 修复 xxx 问题
refactor: 重构 xxx 模块
data: 更新数据/爬虫
docs: 更新文档
test: 新增测试
```

---

## 六、关键设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| Planner 数量 | 全局唯一 | 只有4个模块，不需要独立Planner |
| Tool 策略 | 能升级就升级 | 减少维护两套逻辑的成本 |
| 真实数据优先级 | 无需登录的API优先 | 降低开发门槛 |
| 开发节奏 | 先架构后数据 | 架构不稳，接了真实数据也要重构 |
| Agent框架 | LangChain (保持) | 团队已熟悉，生态成熟 |
| 前端 | Streamlit (保持) | 第一版够用 |

---

## 七、风险与应对

| 风险 | 影响 | 应对方案 |
|------|------|---------|
| Planner 生成计划质量差 | 执行结果不对 | plan validation；低质量降级到简单ReAct |
| catalog API 不稳定 | 真实数据获取失败 | 保留模拟数据fallback；加缓存层 |
| 评课社区反爬 | 选课数据不足 | 降低爬取频率；预置数据兜底 |
| Plan-and-Execute 响应慢 | 用户体验差 | 简单查询走快速通道 |
| LLM API 限流 | Agent调用失败 | 重试 + 本地缓存高频结果 |
