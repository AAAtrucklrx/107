# -*- coding: utf-8 -*-
"""eco:echo —— 注册协议 v1 自检用例（回显输入）。"""


def run(params: dict, ctx: dict) -> dict:
    message = str(params.get("message", "")).strip()
    if not message:
        return {"error": "message 参数不能为空"}
    return {
        "echo": message,
        "student_id": ctx.get("student_id", ""),
        "source": "第三方工具（协议自检）·小蜗开发组",
    }
