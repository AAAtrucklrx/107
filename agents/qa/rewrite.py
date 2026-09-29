"""查询改写 / 指代消解（2026-09-29）。

多轮对话里的省略句（"那门课呢""它老师怎么样""明天呢"）靠单句分类与检索都吃亏。
这里用一次**极短**的 LLM 调用把当前问句改写成自洽问句（含上一轮主语），规则如下：

- **关思考**：`create_llm()` 默认已设 `extra_body.thinking.type=disabled`（utils/llm_client.py），
  所以本步天然不开思考，只做"改写"这种轻任务；
- **触发式**：只有"有历史 + 省略句/指代"才调用，其余情况零开销（见 `should_rewrite`）；
- **不阻塞**：调用方用线程并行发起，`RewriteWorker.rewrite(..., deadline=...)` 到点即返回；
  超时/异常/输出异常一律**回退原问句**（fail-open，绝不拖慢答题）；
- **省钱**：max_tokens 很小，prompt 只给最近两轮。
"""

from __future__ import annotations

import re
import time

from utils.logger import get_logger

log = get_logger("xiaowo.rewrite")

_REWRITE_PROMPT = """把用户「当前问句」改写成一句**自洽、可独立理解**的中文问句，补全其中的指代（它/那门课/这个/上面说的）与省略内容；只输出改写后的问句本身，不要解释、不要引号、不要换行。

最近对话：
{history}

当前问句：{query}
改写后："""

# 触发规则：短句（≤8 字）或含指代/追问词
_ELLIPTICAL = ("它", "他", "她", "那门", "那个", "这个", "这些", "上面", "刚才", "继续",
               "还有呢", "呢？", "呢?", "怎么样", "哪个")
_MAX_QUERY_CHARS = 200


def should_rewrite(query: str, history: list[dict] | None) -> bool:
    """是否值得花一次 LLM 改写：必须有历史，且当前问句是省略句/指代句。"""
    text = (query or "").strip()
    if not text or len(text) > _MAX_QUERY_CHARS:
        return False
    has_history = any(
        isinstance(item, dict) and item.get("role") == "user" and item.get("content")
        for item in (history or [])
    )
    if not has_history:
        return False
    if len(text) <= 8:
        return True
    return any(token in text for token in _ELLIPTICAL)


def _clean(text: str, original: str) -> str:
    """清洗模型输出：去引号/前缀/换行；异常或过长一律回退原问句。"""
    value = (text or "").strip()
    value = re.sub(r"^(改写后[:：]|改写[:：]|答案[:：])", "", value).strip()
    value = value.strip("\"'“”「」").strip()
    value = value.splitlines()[0].strip() if value else ""
    if not value or len(value) > _MAX_QUERY_CHARS or len(value) < 2:
        return original
    return value


class RewriteWorker:
    """把多轮省略句改写成自洽问句；无模型或异常时静默回退（不影响答题）。"""

    def __init__(self, model_name: str = "", *, timeout: float = 2.0, invoke=None,
                 enabled: bool | None = None) -> None:
        import os

        self.model_name = (model_name or "").strip()
        self.timeout = max(0.5, float(timeout))
        self._invoke = invoke
        self._model = None
        # 默认启用（用 create_llm 的默认模型）；XIAOWO_REWRITE_ENABLED=0 可一键关掉
        if enabled is None:
            enabled = (os.environ.get("XIAOWO_REWRITE_ENABLED", "1").strip().lower()
                       not in ("0", "false", "no", "off"))
        self._enabled = bool(enabled)

    def enabled(self) -> bool:
        return self._enabled and (self._invoke is not None or True)

    def _call(self, prompt: str) -> str:
        if self._invoke is not None:  # 测试注入
            return str(self._invoke(prompt) or "")
        if self._model is None:
            from utils.llm_client import create_llm

            # create_llm 默认 thinking=disabled（见 utils/llm_client.py）
            self._model = create_llm(temperature=0, model=self.model_name or None)
        out = self._model.invoke(prompt)
        return str(getattr(out, "content", out) or "")

    def rewrite(
        self, query: str, history: list[dict] | None = None, *, deadline: float | None = None
    ) -> tuple[str, float, bool]:
        """返回 (改写后问句, 耗时毫秒, 是否回退)。deadline 是绝对时间戳（time.time()）。"""
        original = (query or "").strip()
        t0 = time.time()
        if not original or not self.enabled():
            return original, 0.0, True
        if deadline is not None and t0 >= deadline:
            return original, 0.0, True  # 预算已经用完，直接不试
        recent = [
            f"{'用户' if item.get('role') == 'user' else '小蜗'}：{str(item.get('content'))[:80]}"
            for item in (history or [])[-4:]
            if isinstance(item, dict) and item.get("content")
        ]
        prompt = _REWRITE_PROMPT.format(history="\n".join(recent) or "（无）", query=original)
        try:
            raw = self._call(prompt)
        except Exception as exc:  # noqa: BLE001 — 改写失败不影响答题
            log.info("查询改写失败，回退原问句: %s: %s", type(exc).__name__, str(exc)[:120])
            return original, round((time.time() - t0) * 1000, 1), True
        value = _clean(raw, original)
        fallback = value == original
        if not fallback:
            log.info("查询改写: %r → %r", original[:24], value[:24])
        return value, round((time.time() - t0) * 1000, 1), fallback
