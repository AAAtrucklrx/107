"""
会话上下文（Phase 2a）——ContextVar 承载"当前请求所属学生"，供全局单例按学生维度隔离。

背景：ServiceContainer 为进程级单例、CASClient 持单一登录态、advisor 画像为模块级全局，
Streamlit 多浏览器会话（多用户）并发时会互相串数据。本模块提供线程内传播的当前学生
标识（ContextVar 随同步调用栈传播，Streamlit 每个会话在线程内同步执行，全程可见）。

约定：
- run_qa / 页面级 CAS 触点等入口处 set_student(...) 后须配对 reset_student(...)。
- 未设置时使用默认键（""），脚本与测试保持进程级共享的旧行为。
"""
from __future__ import annotations

from contextvars import ContextVar

_STUDENT: ContextVar[str] = ContextVar("xiaowo_current_student", default="")


def set_student(student_id: str | None) -> object:
    """设置当前学生上下文，返回 token 供 reset_student 恢复。"""
    return _STUDENT.set(student_id or "")


def reset_student(token: object) -> None:
    """恢复 set_student 之前的上下文。"""
    _STUDENT.reset(token)


def current_student() -> str:
    """当前学生标识；未设置返回 ""（默认键）。"""
    return _STUDENT.get()
