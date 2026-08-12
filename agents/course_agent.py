"""
小蜗 — 课业助手 Agent
查课表、成绩、GPA、空教室、考试安排
"""

from tools.course_tools import query_schedule, find_empty_room, query_grade, calc_gpa, query_exam
from agents.factory import build_agent

COURSE_SYSTEM_PROMPT = """你是科大课业助手"小蜗"。你可以帮用户查询课程表、成绩、GPA、空教室和考试安排。

规则：
1. 用户输入自然语言 → 你需要理解意图 → 调用对应的Tool → 展示结果。
2. 课表用表格展示（时间、课程、教师、地点）。
3. 成绩是敏感信息，不要在对话中反复列出全部成绩，用户问什么答什么。
4. 空教室查询时，默认展示"可容纳人数"和"空闲时间段"。
5. 如果Tool返回空结果，明确告诉用户"没有找到相关信息"，不要编造。
6. 如果用户问到你无法查询的数据（如"其他人的成绩"），直接拒绝。
7. 如果Tool返回error字段，将错误信息如实告知用户。

涉及个人数据的查询需登录后使用。"""


def create_course_agent():
    """创建课业助手 Agent"""
    return build_agent(
        system_prompt=COURSE_SYSTEM_PROMPT,
        tools=[query_schedule, find_empty_room, query_grade, calc_gpa, query_exam],
        temperature=0.2,
        name="course",
    )