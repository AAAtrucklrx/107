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
| **可核验性** | 合成**后** | 答案里的数字/日期是否都在 refs 里 | 先修复 → 再就地对冲（标「资料未给出」）→ 仍不合格才拒答/回退 |

⚠️ 可核验性只覆盖**数字/日期类**事实（人名/地名抓不到，那要靠判官或人工）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
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

# 对冲标记（2026-09-17）：把"证据里没有的数字/日期"**就地**换成它，
# 而不是把整篇回答作废。说明句里**不能出现数字**——否则下一次可核验校验会把它
# 自己当成"无据事实"，又变成拒答。
_NEUTRAL_TAG = "（资料未给出）"
_HEDGE_NOTE = (
    "\n\n> 注：上文标注为「资料未给出」的数字/日期，在本次检索到的资料里没有依据，"
    "小蜗不补写；请以学校官方发布为准。"
)


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


# ── 闸门二：可核验性（合成后，带一次自动修复） ──────────────────────────────
_REPAIR_PROMPT = """你是回答校对员。上一版回答里有若干**数字或日期在检索资料中找不到依据**。

请改写这一版回答：
- 对**没有依据**的那些数字/日期：直接删除，或改成「资料中未给出」这类如实说明；
- 其余内容、结构与语气**保持不变**；
- **不得新增**任何资料里没有的事实，也不要用你的常识补数字。

只输出改写后的回答正文（不要解释你在改什么）。

用户问题：{question}

检索资料：
{references}

上一版回答：
{previous}

无依据的数字/日期：{facts}
"""


def repair_answer(question: str, references: list[dict], previous: str, unsupported: list[str]) -> str:
    """让模型把"无依据的数字/日期"删掉或改成"未给出"（2026-09-17）。

    为什么不在整篇拒答：用户要的是"这块没数据"这种答复，而不是什么都没有。
    实测「期末考试安排」原本因 2 个无据日期被整篇拒答，改一版就能给出可用的回答。
    """
    from langchain_core.prompts import ChatPromptTemplate
    from utils.llm_client import create_llm

    prompt = ChatPromptTemplate.from_messages([
        ("system", _REPAIR_PROMPT),
        ("human", "请输出改写后的回答正文。"),
    ])
    result = (prompt | create_llm(temperature=0)).invoke({
        "question": str(question or "")[:300],
        "references": build_web_candidates_summary(references, limit=8),
        "previous": str(previous or "")[:4000],
        "facts": "、".join(unsupported[:10]),
    })
    return str(getattr(result, "content", result))



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


def neutralize_unsupported(answer: str, unsupported: list[str]) -> tuple[str, list[str]]:
    """把残留的无据数字/日期**就地**换成「（资料未给出）」，返回 (新文本, 实际替换的 token)。

    为什么不对冲就要整篇拒答：实测「中国科学技术大学推免保研条件」纯搜索拿到 8 条 /
    6303 字高度相关的原文，合成基本可用，只因多写了一个资料里没有的比例，就被整篇作废
    成 98 字拒答。用户要的答复是"这块没数据"，那就**只把没数据的那个数字标出来**。
    """
    text = str(answer or "")
    hit: list[str] = []
    for token in unsupported or []:
        token = str(token or "")
        if token and token not in _NEUTRAL_TAG and token in text:
            text = text.replace(token, _NEUTRAL_TAG, 1)
            hit.append(token)
    return text, hit


def _hedge_residual(answer: str, unsupported: list[str], evidence: str) -> tuple[str, list[str]]:
    """反复对冲直到没有残留（同一个 token 可能在正文里出现多次），最多 3 轮。"""
    text = str(answer or "")
    hits: dict[str, None] = {}
    bad = list(unsupported or [])
    for _ in range(3):
        if not bad:
            break
        text, done = neutralize_unsupported(text, bad)
        if not done:
            break
        for token in done:
            hits[token] = None
        bad = unsupported_facts(text, evidence)
    return text, list(hits)


def _local_hint_block(local_hint: dict | None) -> str:
    """本地已确认内容 → 合成提示里的「必须保留」块（2026-09-18）。

    为什么要有"未收录这类话不算事实"那一句：本地那份答案往往一半是事实、一半是
    "院长未收录，建议自行核实"。若不点明，模型会把那句拒答也当成"已确认内容"保留，
    联网查到的院长就白查了。
    """
    if not local_hint:
        return ""
    markdown = str(local_hint.get("markdown") or "").strip()
    if not markdown:
        return ""
    titles = [str(t).strip() for t in (local_hint.get("titles") or []) if str(t).strip()]
    lines = ["", "【本地知识库已确认内容（优先级最高）】", markdown]
    if titles:
        lines.append("（以上内容来自：" + "、".join(titles[:5]) + "；引用时请写明来源名称）")
    lines.append(
        "【合并要求】必须完整保留上述已确认事实（姓名、电话、邮箱、日期等不得改写或省略）；"
        "只补充联网检索到的其余部分；两者冲突时以本地已确认内容为准并说明差异；"
        "上述内容里若有「未收录 / 未查到 / 建议自行核实」这类**没有给出事实**的表述，"
        "而联网检索到了对应信息，请直接用联网结果补全并说明来源，不要保留那种说法。"
    )
    return "\n".join(lines)


def compose_and_verify(
    question: str,
    references: list[dict],
    *,
    max_repair: int = 1,
    report: list[str] | None = None,
    on_delta: "Callable[[str], None] | None" = None,
    local_hint: dict | None = None,
) -> tuple[str, list[str]]:
    """自研合成 + 可核验性校验（**先修复、再对冲，最后才拒答**）。

    三级处理（第三级 2026-09-17 加）：

    1. 合成后发现有依据缺失的数字/日期 → **先让模型改一版**（删掉或写"资料中未给出"）；
    2. 改过仍有残留 → **就地对冲**：把那几个 token 换成「（资料未给出）」并在末尾加一句
       说明；只要答案里**还有至少 1 个可核验的事实**就照常展示；
    3. 残留对冲不掉、或答案里的数字**全都是**无据的（说明数字就是它的全部内容）才拒答。

    证据口径 = 检索披露的标题/URL/正文 **+ 我们注入的日期/学期上下文**。
    `report` 非空时把"对冲了几处"写回去，供调用方记进 limitations。
    """
    # 只有**首次合成**流式：修复/对冲会重写正文，再流一次会让用户看到内容跳来跳去
    answer = compose_with_own_llm(
        question, references, on_delta=on_delta, local_hint=local_hint
    )
    if not answer.strip():
        return "", []
    evidence = refs_evidence(references) + "\n" + injected_context()
    hint_text = str((local_hint or {}).get("markdown") or "").strip()
    if hint_text:
        # 本地已确认内容也是**合法证据**：否则里面的电话/邮箱会被可核验闸门当成
        # "无据数字"对冲掉（2026-09-18 实测场景：教秘电话在本地、联网只抓到标题）
        evidence = evidence + "\n" + hint_text
    unsupported = unsupported_facts(answer, evidence)
    attempts = 0
    while unsupported and attempts < max(0, max_repair):
        attempts += 1
        try:
            repaired = repair_answer(question, references, answer, unsupported)
        except Exception as exc:  # noqa: BLE001 —— 修复失败不该把整条链路打挂
            log.warning(f"答案修复失败（保留原答案交由调用方判断）: {exc}")
            break
        if not repaired.strip():
            break
        answer = repaired
        unsupported = unsupported_facts(answer, evidence)
    if unsupported:
        hedged, hit = _hedge_residual(answer, unsupported, evidence)
        supported = len(evidence_facts(answer)) - len(unsupported)
        residual = unsupported_facts(hedged, evidence)
        if hit and not residual and supported >= 1:
            answer = hedged + _HEDGE_NOTE
            if report is not None:
                report.append(
                    f"答案里有 {len(hit)} 处具体数字/日期在检索资料中没有依据，"
                    "已就地标注为「资料未给出」。"
                )
            # 可观测：以后能数出"对冲"多久发生一次（拒答率下降的代价就是它）
            log.info(
                f"可核验性对冲：{len(hit)} 处无据数字/日期已标注为「资料未给出」"
                f"（{'、'.join(hit[:3])}）"
            )
            unsupported = []
        else:
            unsupported = residual or unsupported
    return answer, unsupported


def compose_with_own_llm(
    question: str,
    references: list[dict],
    *,
    on_delta: "Callable[[str], None] | None" = None,
    local_hint: dict | None = None,
) -> str:
    """用**我们自己的** COMPOSE_PROMPT 与 LLM 合成答案（证据每条 1500 字）。

    `on_delta` 非空时走**真流式**（2026-09-18）：逐 chunk 推给调用方（缓冲约 16 字），
    首字延迟从"整篇生成完"降为"首个 token"；流式失败且一分内容都没出时，回退一次性
    `.invoke()` 保证仍有答案。**注意**：流出去的是**未过闸门**的稿子，最终由
    `compose_and_verify` 的返回值（可能经修复/对冲改写）覆盖。
    """
    from agents.qa.nodes import (
        COMPOSE_PROMPT,
        _current_date_text,
        _current_weekday_text,
        _semester_context_text,
    )
    from langchain_core.prompts import ChatPromptTemplate
    from utils.llm_client import create_llm, llm_content

    prompt = ChatPromptTemplate.from_messages([
        ("system", COMPOSE_PROMPT),
        ("human", "请直接输出回答正文，第一句必须是面向用户的内容。"
                  + _local_hint_block(local_hint)),
    ])
    invoke_vars = {
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
    }
    chain = prompt | create_llm(temperature=0.3)
    if on_delta is None:
        result = chain.invoke(invoke_vars)
        return str(getattr(result, "content", result))
    parts: list[str] = []
    buf: list[str] = []
    try:
        for chunk in chain.stream(invoke_vars):
            # strip=False：流式增量不得逐块 strip，否则块边界换行被吃掉，
            # Markdown 表格/列表会塌成一行（2026-09-15 在本地链路踩过同样的坑）
            delta = llm_content(chunk, strip=False)
            if delta:
                parts.append(delta)
                buf.append(delta)
                if sum(len(b) for b in buf) >= 16:
                    on_delta("".join(buf))
                    buf = []
        if buf:
            on_delta("".join(buf))
    except Exception as exc:  # noqa: BLE001 —— 流式中断不该把整条链路打挂
        log.warning(f"联网合成流式中断（保留已生成 {sum(len(p) for p in parts)} 字）: {exc}")
    text = "".join(parts)
    if text.strip():
        return text
    # 一分内容都没流出来（provider 不支持 stream / 首个 chunk 前就失败）→ 退回一次性调用
    result = chain.invoke(invoke_vars)
    return str(getattr(result, "content", result))
