"""联网答案的**自研合成**与两道质量闸门（2026-09-17）。

为什么不再用端点的"智能搜索生成"直接出答案（评估文档 §6 的结论）：

- 它是**黑盒**：答案里大量数字**不在它披露给我们的证据里**（实测可核验比例 0.342：
  学费 8000/5000/3000、电话、推免分数…），可能是真的但我们**无法核验**；
- 它只给我们 **203 字/条**的证据，却用内部更全的上下文作答 ——「答案 ≠ 我们能看到的依据」。

自研合成的前提是**答案只能来自检索到的原文摘录**（纯搜索模式给 1000~1500 字/条），
这样才可能对答案做硬校验。两道闸门：

| 闸门 | 时机 | 判定 | 不通过时 |
|---|---|---|---|
| **相关性** | 合成**前** | refs 是否与问题相关、足以回答 | 不合成，走 B3 诚实拒答（→ runner 回退本地） |
| **可核验性** | 合成**后** | 答案里的数字/日期是否都在 refs 里 | 同样拒答/回退，**绝不展示无据答案** |

⚠️ 可核验性只覆盖**数字/日期类**事实（人名/地名抓不到，那要靠判官或人工）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)

# 给合成的证据上限：本地知识库路径每条只给 120 字，联网场景必须放开，
# 否则纯搜索多拿到的原文（1000+ 字/条）等于白拿。
_WEB_CANDIDATE_CHARS = 1500


def build_web_candidates_summary(references: list[dict], *, limit: int = 8) -> str:
    """把联网 references 拼成候选摘要（每条最多 1500 字），供自研合成使用。"""
    lines: list[str] = []
    for index, ref in enumerate(references[:limit], start=1):
        title = str(ref.get("title") or "未命名").strip()[:120]
        url = str(ref.get("url") or "").strip()
        date = str(ref.get("date") or "").strip()
        content = str(ref.get("content") or "").strip()[:_WEB_CANDIDATE_CHARS]
        head = f"[{index}] 《{title}》"
        if date:
            head += f" 日期:{date}"
        if url:
            head += f" 来源:{url}"
        lines.append(f"{head}: {content}")
    return "\n".join(lines) if lines else "（无候选片段）"


# ── 可核验性：答案里的数字/日期必须能在证据里找到 ────────────────────────────
_FACT_RE = re.compile(r"\d[\d:：./\-*×xX]{1,}\d|\d{3,}")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """比对用归一化：NFKC（全角→半角）+ 去空白 + 乘号/字母 x 统一。

    实测踩到：答案写 `7×16`、证据写 `7*16`，不归一化会**误判为无依据**并拒掉合法答案。
    """
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.replace("×", "*").replace("✕", "*").replace("✖", "*")
    normalized = normalized.replace("x", "*").replace("X", "*")
    return _WS_RE.sub("", normalized)


def refs_evidence(references: list[dict]) -> str:
    """一次检索**披露出来的全部内容**：标题 + URL + 正文（URL 里的数字也算依据）。"""
    parts: list[str] = []
    for ref in references or []:
        if not isinstance(ref, dict):
            continue
        parts.append(str(ref.get("title") or ""))
        parts.append(str(ref.get("url") or ""))
        parts.append(str(ref.get("content") or ""))
    return "\n".join(parts)


def evidence_facts(answer: str) -> list[str]:
    """从答案里抽可核对的事实片段（数字/日期类），去重且保持出现顺序。"""
    seen: dict[str, None] = {}
    for match in _FACT_RE.finditer(str(answer or "")):
        token = match.group(0).strip()
        if len(token) >= 2 and token not in seen:
            seen[token] = None
    return list(seen)


def unsupported_facts(answer: str, evidence: str) -> list[str]:
    """答案里出现、但证据里找不到的事实片段（归一化后比对）。"""
    haystack = _normalize(evidence)
    return [f for f in evidence_facts(answer) if _normalize(f) not in haystack]


def verifiable_ratio(answer: str, evidence: str) -> float | None:
    """可核验比例 = 1 - 未命中事实 / 全部事实；无数字事实可核对时返回 None。"""
    facts = evidence_facts(answer)
    if not facts:
        return None
    return round(1 - len(unsupported_facts(answer, evidence)) / len(facts), 3)


# ── 闸门一：相关性（合成前） ────────────────────────────────────────────────
_RELEVANCE_PROMPT = """你是检索质量判官。判断下面的检索资料**是否与用户问题有关、可以据以作答**。

判定口径（2026-09-17 收紧后放宽过一次：过严会把能答的题也拒掉，实测 10 题里误拒 4 题）：

- 资料讨论的是**别的话题**（例：问食堂开放时间，资料全是选课、竞赛、招生）→ `no`
- 资料与问题**属于同一主题**——哪怕只覆盖一部分、或偏概述性介绍 → **`yes`**
  （答得全不全、有没有编造，交给后续合成与可核验性闸门判断，不要在这一步代劳）
- 资料为空、或只有站点导航/栏目列表 → `no`

只输出 JSON：{{"relevant":"yes|no","reason":"一句话中文说明"}}

用户问题：{question}

检索资料：
{references}
"""


class _RelevanceVerdict:
    """结构化输出用（pydantic 模型在函数内建，避免模块级依赖）。"""


def judge_relevance(question: str, references: list[dict], *, max_chars: int = 400) -> bool | None:
    """判断 refs 是否值得拿去合成。返回 None = 判不出来（调用方按"不合成"处理，fail-closed）。

    为什么要这道闸门：实测「食堂开放时间」这题，纯搜索返回的证据全是**教务处选课/竞赛**
    之类无关内容，而合成模型照样写了一篇带具体细节的答案（判官判"缺乏证据支撑"）。
    当前智能搜索之所以没出这种事，是靠它自己的严格提示词——换成自研合成后，
    这份保护必须由我们**显式**提供。
    """
    usable = [r for r in (references or []) if str(r.get("content") or "").strip()]
    if not usable:
        return False
    from pydantic import BaseModel

    class Verdict(BaseModel):
        relevant: str = "no"
        reason: str = ""

    body = "\n\n".join(
        f"[{index}] {str(ref.get('title') or '')[:60]}\n{str(ref.get('content') or '')[:max_chars]}"
        for index, ref in enumerate(usable[:8], start=1)
    )
    try:
        from langchain_core.prompts import ChatPromptTemplate  # noqa: F401  (保持依赖显式)
        from utils.llm_client import create_llm

        structured = create_llm(temperature=0).with_structured_output(Verdict, method="json_mode")
        verdict = structured.invoke(
            _RELEVANCE_PROMPT.format(question=str(question or "")[:300], references=body)
        )
    except Exception as exc:  # noqa: BLE001 —— 判不出来时 fail-closed（不合成）
        log.warning(f"相关性闸门调用失败，按不相关处理: {exc}")
        return None
    token = str(getattr(verdict, "relevant", "") or "").strip().casefold()
    if token.startswith("y") or "是" in token or "相关" in token:
        return True
    return False


# ── 闸门二：可核验性（合成后） ──────────────────────────────────────────────
def injected_context() -> str:
    """我们**主动注入**给合成的上下文（当前日期/星期/学期与教学周日期对照）。

    ⚠️ 校验答案时必须把它算进"证据"：compose 提示词本来就会带上这些变量，
    模型完全可能合法地写出「2027 春」「12-14 那周」这类日期。实测踩到过——
    不带上它，`转专业`/`推免` 这类含有学期日期的正确答案会被误判为"无据"而拒掉。
    """
    try:
        from agents.qa.nodes import (
            _current_date_text,
            _current_weekday_text,
            _semester_context_text,
        )

        return "\n".join(
            [_current_date_text(), _current_weekday_text(), _semester_context_text()]
        )
    except Exception:  # noqa: BLE001
        return ""


def compose_and_verify(
    question: str, references: list[dict]
) -> tuple[str, list[str]]:
    """自研合成 + 可核验性校验。返回 (答案, 未获证据支持的事实列表)。

    证据口径 = 检索披露的标题/URL/正文 **+ 我们注入的日期/学期上下文**。
    未支持列表**非空**时调用方必须拒答/回退——宁可说"没查到"，也不展示无据内容。
    """
    answer = compose_with_own_llm(question, references)
    if not answer.strip():
        return "", []
    evidence = refs_evidence(references) + "\n" + injected_context()
    return answer, unsupported_facts(answer, evidence)


def compose_with_own_llm(question: str, references: list[dict]) -> str:
    """用**我们自己的** COMPOSE_PROMPT 与 LLM 合成答案（证据每条 1500 字）。"""
    from agents.qa.nodes import (
        COMPOSE_PROMPT,
        _current_date_text,
        _current_weekday_text,
        _semester_context_text,
    )
    from langchain_core.prompts import ChatPromptTemplate
    from utils.llm_client import create_llm

    prompt = ChatPromptTemplate.from_messages([
        ("system", COMPOSE_PROMPT),
        ("human", "请直接输出回答正文，第一句必须是面向用户的内容。"),
    ])
    result = (prompt | create_llm(temperature=0.3)).invoke({
        "query": question,
        "current_date": _current_date_text(),
        "current_weekday": _current_weekday_text(),
        "semester_context": _semester_context_text(),
        "intent": "知识问答",
        "chat_history": "（无）",
        "student_info": "（未登录）",
        "candidates_summary": build_web_candidates_summary(references, limit=8),
        "tool_summary": "（无）",
        "candidates_found": "已达到匹配阈值",
        "structured_note": "本回答无结构化数据卡，请按上述规则生成正文。",
    })
    return str(getattr(result, "content", result))
