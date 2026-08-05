"""
小蜗 — 跨 Step 上下文管理器
在 Plan-and-Execute 架构中，存储和管理多步执行之间的共享数据
"""

from __future__ import annotations

from utils.logger import get_logger

log = get_logger("xiaowo.context")


class Context:
    """
    跨 Step 共享的上下文容器。

    两层数据：
    - 会话级（chat_history, user_profile）：跨多次 Plan 执行保持
    - 计划级（step_results, intermediate_facts）：每次 Plan 执行时重置
    """

    def __init__(self) -> None:
        # 会话级数据
        self.chat_history: list[dict] = []
        self.user_profile: dict = {}

        # 计划级数据（每次 Plan 执行前重置）
        self.step_results: dict[int, dict] = {}
        self.intermediate_facts: dict[str, object] = {}

    # ── Step 结果管理 ────────────────────────────

    def add_step_result(self, step_id: int, result: dict) -> None:
        """存储某 Step 的 Tool 返回结果，并自动提取关键事实"""
        self.step_results[step_id] = result
        self._extract_facts(step_id, result)
        log.debug(f"Step {step_id} 结果已存入, 提取 {len(self._get_step_facts(step_id))} 个事实")

    def get_step_result(self, step_id: int) -> dict | None:
        """获取某 Step 的结果"""
        return self.step_results.get(step_id)

    def get_all_results(self) -> dict[int, dict]:
        """获取所有 Step 的结果"""
        return dict(self.step_results)

    # ── 事实提取与引用 ──────────────────────────

    def _extract_facts(self, step_id: int, result: dict) -> None:
        """
        从 Tool 结果中提取关键事实，供后续 Step 引用。
        提取规则：遍历 result dict，将基本类型值存为 step_{id}.{key}
        """
        if not isinstance(result, dict):
            return
        for key, value in result.items():
            # 只提取基本类型（str, int, float, bool），跳过嵌套结构和列表
            if isinstance(value, (str, int, float, bool)):
                fact_key = f"step_{step_id}.{key}"
                self.intermediate_facts[fact_key] = value
            # 对列表提取 count
            elif isinstance(value, list):
                self.intermediate_facts[f"step_{step_id}.{key}_count"] = len(value)

    def _get_step_facts(self, step_id: int) -> dict[str, object]:
        """获取某 Step 提取出的所有事实"""
        prefix = f"step_{step_id}."
        return {k: v for k, v in self.intermediate_facts.items() if k.startswith(prefix)}

    def get_fact(self, fact_key: str) -> object | None:
        """获取指定事实的值，如 'step_1.gpa'"""
        return self.intermediate_facts.get(fact_key)

    def resolve_placeholder(self, value: str) -> str:
        """
        解析字符串中的占位符 {step_N.field}，替换为实际值。
        例如: "{step_1.gpa}" → "3.53"
        """
        import re
        pattern = r'\{step_(\d+)\.(\w+)\}'

        def replacer(match: re.Match) -> str:
            key = f"step_{match.group(1)}.{match.group(2)}"
            val = self.intermediate_facts.get(key)
            if val is not None:
                return str(val)
            # 未找到，保留原占位符
            return match.group(0)

        return re.sub(pattern, replacer, str(value))

    def resolve_args(self, args: dict) -> dict:
        """递归解析 args dict 中的所有字符串占位符"""
        resolved = {}
        for key, value in args.items():
            if isinstance(value, str):
                resolved[key] = self.resolve_placeholder(value)
            elif isinstance(value, dict):
                resolved[key] = self.resolve_args(value)
            else:
                resolved[key] = value
        return resolved

    # ── 上下文摘要 ──────────────────────────────

    def get_summary(self) -> str:
        """生成上下文摘要，供 LLM 参考"""
        lines = ["=== 已执行的步骤结果 ==="]
        for step_id in sorted(self.step_results.keys()):
            result = self.step_results[step_id]
            # 简洁表示，截取关键字段
            if isinstance(result, dict):
                summary_parts = []
                for k, v in result.items():
                    if isinstance(v, (str, int, float, bool)):
                        summary_parts.append(f"{k}={v}")
                    elif isinstance(v, list):
                        summary_parts.append(f"{k}=[{len(v)}项]")
                lines.append(f"  Step {step_id}: {', '.join(summary_parts)}")
            else:
                lines.append(f"  Step {step_id}: {str(result)[:100]}")

        if self.intermediate_facts:
            lines.append("\n=== 提取的关键事实 ===")
            for k, v in sorted(self.intermediate_facts.items()):
                lines.append(f"  {k} = {v}")

        return "\n".join(lines)

    # ── 生命周期管理 ────────────────────────────

    def reset_plan_context(self) -> None:
        """新 Plan 开始前重置计划级数据（保留会话级数据）"""
        self.step_results.clear()
        self.intermediate_facts.clear()
        log.debug("计划级上下文已重置")

    def add_chat_history(self, role: str, content: str) -> None:
        """追加对话历史"""
        self.chat_history.append({"role": role, "content": content})
        # 保留最近 20 条，避免上下文过长
        if len(self.chat_history) > 20:
            self.chat_history = self.chat_history[-20:]
