"""小蜗开发流水线 — 图状态定义。"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class PipelineState(TypedDict, total=False):
    # 输入
    task: str                      # 开发任务描述
    executor: str                  # claude | codex
    max_rounds: int                # 循环上限

    # 计划
    plan: str                      # 开发计划
    project_context: str           # 项目快照（git 状态/结构）

    # 执行
    round: int                     # 当前轮次（从 1 开始）
    changes: list[dict]            # 每轮执行摘要 [{round, executor, diff_summary, files}]
    exec_output: str               # 最近一轮 CLI 原始输出
    files: list[str]               # 最近一轮变更文件列表
    diff_stat: str                 # 最近一轮变更统计文本

    # 测试
    test_output: str               # 最近一轮测试输出
    test_summary: str              # 测试摘要（LLM 提炼）

    # 报告
    report_md: str                 # markdown 报告
    canvas_path: str               # 生成的 canvas 报告路径

    # 决策
    verdict: str                   # pass | fail
    issues: list[str]              # 发现的问题
    fix_instruction: str           # 传给下一轮执行的修改指令

    # 运行
    messages: Annotated[list, add_messages]  # LangGraph 消息通道（调试用）
