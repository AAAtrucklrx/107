"""
小蜗 — 日程管理 Agent
整合课表 + 作业 + 考试 + 自定义事件，智能提醒、冲突检测
"""

from tools.schedule_tools import add_event, get_day_view, get_week_view, check_conflict, import_schedule
from agents.factory import build_agent

SCHEDULE_SYSTEM_PROMPT = """你是科大日程管家"小蜗"。你可以帮用户管理日程：添加事件、查看日程、检测冲突、导入课表。

规则：
1. 用户用自然语言描述日程 → 你需要解析时间 → 调用 add_event 工具。
2. 添加日程后，如果检测到冲突，友善提醒用户（用"注意"而不是"警告"）。
3. 查看日程时，按时间顺序展示，分为课程和自定义事件两类。
4. 日程管理体现"今天应该被温柔地安排好"的设计理念——像慢慢流动的河水，而不是高压任务管理器。
5. 如果自然语言解析时间失败，请用户说得更具体。
6. 如果用户想添加过去时间的日程，提示"这个时间已经过去了"。
7. 拒绝跨天事件添加（超过24小时的事件提示"跨天事件暂不支持"）。

学生ID默认使用 PB20240001。"""


def create_schedule_agent():
    """创建日程管理 Agent"""
    return build_agent(
        system_prompt=SCHEDULE_SYSTEM_PROMPT,
        tools=[add_event, get_day_view, get_week_view, check_conflict, import_schedule],
        temperature=0.2,
        name="schedule",
    )