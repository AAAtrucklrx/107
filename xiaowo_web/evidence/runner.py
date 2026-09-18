"""Automatic local-first gate with a strict Web evidence fallback."""

from __future__ import annotations

import asyncio
import inspect
import re

from utils.logger import get_logger

from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.chat.runner import QaRunner, chitchat_reply, is_chitchat_query
from xiaowo_web.evidence.pipeline import EvidencePipeline

log = get_logger(__name__)


_CURRENT_TERMS = re.compile(r"(?:最新|最近|近期|近日|最近几天|今天|现在|当前|截至|刚刚|本周|本月|今年|目前|现行|还有效吗)")


_TOOL_INTENTS = frozenset({
    "查成绩", "查课表", "查考试", "查空教室", "日程查询", "日程管理",
    "选课冲突", "退补选评估", "课程搜索", "选课推荐", "教师点评",
})


def _likely_local_tool_answer(question: str) -> bool:
    """预判：意图为个人数据/课程工具类 → 本地工具结果权威，无需并发联网。"""
    try:
        from knowledge.intent_classifier import classify
        return (classify(question or "").get("intent") or "") in _TOOL_INTENTS
    except Exception:
        return False


# 需要「用户本人数据」的问句特征：第一人称领属 + 自己的课程/成绩。
# **不能用 `_likely_local_tool_answer`**——它是给并行预判用的启发式，实测对
# "今天的新闻" 也返回 True，会把本该联网的时效问题误拦（2026-09-16 踩到）。
_PERSONAL_NEED_KW = ("我的", "我目前", "我现在", "我选的", "我修的", "我已修",
                     "帮我对比我", "对比下我")
# 本地回答已经在要求登录 → 联网无从补足，它就是权威答案
_LOGIN_NEED_MARKERS = ("未登录", "没有登录", "请登录", "登录后", "需要登录")


# 匿名短路里用来判「这题在问**外部时效事实**」的更窄时效词。
# **不含**"今天/现在/当前/目前"——它们在个人问句里常是"我现在的课表""我目前选的课"，
# 与"外部信息是否过期"无关；直接复用 `_CURRENT_TERMS` 会把"我目前选的课…"判成时效问题，
# 短路失效（2026-09-16 自查发现）。
_HARD_CURRENT_TERMS = re.compile(
    r"(?:最新|最近|近期|近日|截至|刚刚|本周|本月|今年|现行|还有效吗)"
)


def _needs_personal_data(question: str, local_markdown: str = "") -> bool:
    """问句是否在要「用户本人的数据」（未登录时联网根本查不到）。

    只在两种证据下判真：问句出现第一人称领属词，或本地回答已明确要求登录。
    通用政策类时效问题（"最新的转专业政策"）**不**算——那类正需要联网核验。"""
    q = question or ""
    if any(k in q for k in _PERSONAL_NEED_KW):
        return True
    return any(m in (local_markdown or "") for m in _LOGIN_NEED_MARKERS)


# 「本地其实没答出来」的否定式措辞。**只看答案开头**（通常首句就交代结论），
# 避免误伤"答出来了但附带说明"的回答（如"国庆放假 3 天……校历未列出具体日期"）。
# 只用**强信号**：都表示"压根没检索到东西"。刻意**不收**"暂无/暂时没有/无法确认/
# 超出范围"这类弱措辞——它们常出现在**答出来了、只是某个细节没明确**的回答里
# （实测「国庆放假3天……具体日期暂无」被误判成"没答出来"，丢掉了正确的本地校历）。
_NO_ANSWER_RE = re.compile(
    r"(?:未(?:能)?找到|没有找到|未检索到|没有检索到|未能查到|查不到|没有查到|"
    r"没有能(?:够)?查到|没查到|没找到|未能找到|无法查到)"
)
_NO_ANSWER_PREFIX_CHARS = 150


_ANSWER_JUDGE_PROMPT = """你是判定器：判断「助手回答」是否**真的回答了**「用户问题」。

只输出两个字之一：能 / 不能
- 回答给出了问题所问的具体信息（哪怕同时附带说明或补充）→ 能
- 回答只是说没查到 / 暂无 / 无法确认 / 不在范围内，或答非所问，或要求用户先登录、
  先补充信息 → 不能
- 问题包含**多个并列的询问点**（如"A 和 B 分别是谁""A 的联系方式和办公地点"）时，
  只要其中有**任何一项**没有正面回答（只说没查到 / 未收录 / 建议自行核实），就判「不能」
  —— 缺项必须交给联网补齐，不能因为"答出了一部分"就算答出来了

用户问题：
{q}

助手回答：
{a}

只输出：能 或 不能"""


_JUDGE_HEAD_CHARS = 1200
_JUDGE_TAIL_CHARS = 1200


def _judge_window(text: str) -> str:
    """判定器必须**看到答案的头和尾**。

    实测（2026-09-18）：问「龚伟老师评价，还有龚伟老师的实验室是什么」，回答里
    "实验室这次没检索到"那句在 800 字之后，被原来的 `answer[:800]` 截掉 → 判定器
    只看到评价那半段，判「能」→ **不联网**，缺项永远补不上。
    """
    body = str(text or "")
    if len(body) <= _JUDGE_HEAD_CHARS + _JUDGE_TAIL_CHARS:
        return body
    return body[:_JUDGE_HEAD_CHARS] + "\n…（中间略）…\n" + body[-_JUDGE_TAIL_CHARS:]


def _llm_judge_answered(question: str, answer: str) -> bool | None:
    """LLM 判定「这段回答是否真的回答了问题」。失败返回 None（由调用方退回启发式）。

    为什么需要它（2026-09-16）：本地会把「我这边暂时没有查到…」也标成 `claim=confirmed`，
    于是被"本地优先"直接返回、永不联网。而**别的判据都不够用**：

    - **结构信号**：`top_score` 实测在「答得出/答不出」两组几乎重叠
      （0.50~0.60 vs 0.44~0.58），设任何阈值都会误伤；`candidates_found` 恒为 True；
    - **文本措辞**：打地鼠——本地把"没有查到"换成"没有能查到"就漏了。

    故用一次极短调用做语义判定。判定器本身不可用时返回 None，绝不阻断主链路。
    """
    try:
        from langchain_core.prompts import ChatPromptTemplate

        from utils.llm_client import create_llm, llm_content

        prompt = ChatPromptTemplate.from_messages([("human", _ANSWER_JUDGE_PROMPT)])
        text = llm_content((prompt | create_llm(temperature=0.0)).invoke({
            "q": (question or "")[:500],
            "a": _judge_window(answer),
        })) or ""
    except Exception as exc:  # noqa: BLE001 —— 判定器不可用不得影响回答
        log.warning(f"回答判定器调用失败，退回措辞启发式: {exc}")
        return None
    verdict = str(text).strip()
    if "不能" in verdict:
        return False
    if "能" in verdict:
        return True
    return None


_NEEDS_WEB_JUDGE_PROMPT = """你是判定器：判断这个问题**是否需要联网获取最新信息**。

只输出三个字之一：需要 / 不需要
- 问「最新 / 有没有变化 / 现在怎么样 / 今年 / 近期 / 调整 / 新政 / 通知 / 安排 / 实时数据」→ 需要
- 稳定的常识、概念解释、历史事实、校内固定制度（培养方案条文、校历规则、办事流程）→ 不需要

问题：
{q}

只输出：需要 或 不需要"""


def _llm_judge_needs_web(question: str) -> bool | None:
    """LLM 判定「这题是否需要联网」。失败返回 None（= 保持既有行为）。

    用途（2026-09-16）：`_is_world_query` 靠「不含时效词 + 意图∈{知识问答,活动推荐}」
    判定"世界知识"，会把**隐含时效**的问题（"……有什么**新变化**"）也判进去，
    于是走 LLM 记忆回答、**永不联网**（实测「国家助学贷款新政」就是这样）。
    时效词表**永远列不全**——这已是同类模式的第二次翻车（先漏"没有能查到"、
    再漏"新变化"），故把语义判断交给 LLM。

    ⚠️ **单向**：调用方只在 `_is_world_query` 已判为真时才问它，且只允许把
    「世界知识」改成「联网」，**不允许反向**；判定器失败（None）同样保持原判。
    所以它**不会比现状更糟**。时效词表作为"世界知识"的前置门槛继续保留。
    """
    try:
        from langchain_core.prompts import ChatPromptTemplate

        from utils.llm_client import create_llm, llm_content

        prompt = ChatPromptTemplate.from_messages([("human", _NEEDS_WEB_JUDGE_PROMPT)])
        text = llm_content((prompt | create_llm(temperature=0.0)).invoke({
            "q": (question or "")[:500],
        })) or ""
    except Exception as exc:  # noqa: BLE001 —— 判定器不可用不得影响回答
        log.warning(f"联网需求判定器调用失败，保持既有世界知识判定: {exc}")
        return None
    verdict = str(text).strip()
    if "不需要" in verdict:
        return False
    if "需要" in verdict:
        return True
    return None


async def _local_answered(bundle, question: str) -> bool:
    """本地是否**真的**答出了内容（结构信号 → LLM 判定 → 措辞兜底）。

    判据优先级：
    1. **前置豁免**（直接算"答出来了"，不浪费判定调用）：
       - claim 未全 confirmed：本来就会被 `local_ready` 拦下，不参与；
       - 有 `tool_result`/`tool_cache` 来源：「考试 0 场」是工具的真实结果，联网更查不到；
       - 问句在要用户个人数据：未登录时本地答「请登录」，联网同样拿不到。
    2. **结构信号（可靠但覆盖窄）**：`retrieval["candidates_found"] is False`
       → 知识库压根没召回材料。
    3. **LLM 判定（主判据）**：见 `_llm_judge_answered`。
    4. **措辞（兜底）**：仅当判定器不可用时。
    """
    if not (bundle.claims and all(c.get("status") == "confirmed" for c in bundle.claims)):
        return True
    if any((s.get("level") or "") in {"tool_result", "tool_cache"} for s in (bundle.sources or [])):
        # 工具结果确实权威，但它**只覆盖自己那部分**：多并列问句（"X 的评价，还有 X 的实验室"）
        # 里工具只答了一半时不能据此认定"答出来了"——否则判定器根本不会被调用、永不联网
        # （2026-09-18 实测："实验室"那半句永远补不上）
        if not _looks_multi_part(question):
            return True
    if _needs_personal_data(question, bundle.markdown or ""):
        return True
    retrieval = getattr(bundle, "retrieval", None) or {}
    if retrieval and retrieval.get("candidates_found") is False:
        return False
    judged = await asyncio.to_thread(_llm_judge_answered, question, bundle.markdown or "")
    if judged is not None:
        return judged
    return not bool(_NO_ANSWER_RE.search((bundle.markdown or "")[:_NO_ANSWER_PREFIX_CHARS]))


_LOCAL_HINT_MAX_CHARS = 1800

# 多并列询问点（"X 的评价，还有 X 的实验室"）：工具只覆盖它自己那部分，
# 这类问句不能走"有工具结果就算答出来了"的快速豁免
_MULTI_PART_HINTS = re.compile(r"(还有|以及|另外|顺便|同时|分别|各自|各是)")

# 个人数据工具：其工具结果**绝不**带进联网检索与合成（隐私红线）。
# 反过来，公开工具（analyze_teacher / get_course_reviews / compare_courses / find_empty_room …）
# 的结果必须能带过去，否则"评价 + 实验室"这类复合问题在联网覆盖时会丢掉评价那半段。
_PERSONAL_HINT_TOOLS = frozenset({
    "query_schedule", "query_daily_schedule", "query_grade", "calc_gpa", "query_exam",
    "query_course_selection", "query_program", "get_my_program", "get_program_progress",
    "plan_semester", "import_schedule", "get_week_view", "get_day_view", "add_event",
    "check_conflict", "evaluate_selection_pressure", "check_course_conflict",
})


def _looks_multi_part(question: str) -> bool:
    """问句是否包含**多个并列的询问点**。"""
    return bool(_MULTI_PART_HINTS.search(question or ""))


def _source_is_personal_data(source: dict) -> bool:
    """工具类来源是不是个人数据（成绩/课表/考试/日程…）。

    拿不到工具名时**保守当作个人数据**（旧 bundle / 测试替身）：宁可少合并，也不外泄。
    """
    if str(source.get("level") or "") not in {"tool_result", "tool_cache"}:
        return False
    tool = str(source.get("tool") or "")
    if not tool:
        return True
    return tool in _PERSONAL_HINT_TOOLS


def _local_merge_hint(bundle) -> dict | None:
    """本地已确认内容 → 交给联网合成当"必须保留的基础"（2026-09-18）。

    为什么需要（实测）：问「计算机学院教学秘书和院长分别是谁」，本地答出了教秘
    （姓名/电话/邮箱，official_primary），院长没收录；判定器把整题判成"没答全"后走
    联网，而联网合成只看得到检索结果 → **本地那半段被整段丢掉**，联网又只抓到那个
    页面的标题没抓到正文，最后反过来对教秘说"没法给你"。所以联网时必须把本地已确认
    内容并进去，而不是赢家通吃。

    只带**公共**内容：来源含 `tool_result`/`tool_cache` 的是个人数据（成绩/课表/考试/
    日程），绝不能进联网检索与合成。
    """
    text = str(getattr(bundle, "markdown", "") or "").strip()
    if not text:
        return None
    # 两条本地终态都算"本地已经答出来的内容"：语义缓存命中的也是当初本地路径产出的答案
    # （个人化回答不进缓存，见 xiaowo_web/chat/runner.py::_used_personal_tools）
    if str(getattr(bundle, "terminal_reason", "") or "") not in {"local_answer", "cache_hit"}:
        return None
    sources = list(getattr(bundle, "sources", None) or [])
    if any(_source_is_personal_data(s) for s in sources):
        return None  # 个人数据不进联网
    # 缓存命中会带一条"语义缓存回答"占位来源，不算真来源
    titles = [
        str(s.get("title") or "").strip()
        for s in sources
        if str(s.get("source_id") or "") != "semantic-cache"
    ]
    return {
        "markdown": text[:_LOCAL_HINT_MAX_CHARS],
        "titles": [title for title in titles if title][:5],
    }


def _is_world_query(question: str) -> bool:
    """非校内通用常识判定（延迟导入 agents，避免启动期依赖图）。
    时效词（最新/今天/现状等）仍走联网证据链，避免世界知识给出过期信息。"""
    if _CURRENT_TERMS.search(question or ""):
        return False
    try:
        from agents.qa.nodes import is_world_knowledge_query
        return is_world_knowledge_query(question)
    except Exception:
        return False


def _world_answer(question: str) -> AnswerBundle:
    """世界知识回答（LLM 常识 + 免责标注）；失败降级固定文案。"""
    from agents.qa.nodes import world_knowledge

    try:
        result = world_knowledge({"query": question, "llm_down": False, "world_knowledge": True})
        answer = str(result.get("answer") or "").strip()
    except Exception:
        answer = ""
    if not answer:
        answer = "这是通用知识问题，小蜗暂时无法核实准确信息；你可以换个更具体的问题，或让我联网查询。"
    return AnswerBundle(
        markdown=answer,
        claims=[{
            "claim_id": "c1", "text": answer, "kind": "factual",
            "status": "insufficient", "evidence": [],
        }],
        sources=[],
        limitations=["通用信息，非联网核实，仅供参考。"] if "非联网核实" not in answer else [],
        terminal_reason="local_answer",
    )


# ── 语义缓存：写"最终展示的答案"（2026-09-18） ────────────────
# 为什么写入点要在这里：本地 runner 写缓存时还不知道自己会不会被联网答案覆盖，实测
# 缓存里因此存了一堆"用户没看到的草稿"（例：`2026年暑期社会实践立项通知` 缓存的是本地
# 353 字"未收录"，展示的却是联网 1193 字）。最终答案只在这里才定下来。
_CACHE_TIME_SENSITIVE = re.compile(
    r"(?:最新|最近|近期|近日|最近几天|今天|现在|当前|截至|刚刚|本周|本月|今年|目前|现行|还有效吗)"
)
_CACHE_PERSONAL_LEVELS = frozenset({"tool_result", "tool_cache"})
_CACHE_TRUSTED_LEVELS = frozenset(
    {"official_primary", "reliable_independent", "local_curated", "local"}
)


def _cache_kind(bundle) -> str | None:
    """最终答案要不要进语义缓存：返回 'local' / 'web' / None（不缓存）。"""
    if not str(getattr(bundle, "markdown", "") or "").strip():
        return None
    if bool(getattr(bundle, "truncated", False)):
        return None  # 截断的半截答案不能进缓存
    sources = list(getattr(bundle, "sources", None) or [])
    if any((s.get("level") or "") in _CACHE_PERSONAL_LEVELS for s in sources):
        return None  # 个人数据（成绩/课表/考试/日程）绝不进缓存
    statuses = {str(c.get("status") or "") for c in (getattr(bundle, "claims", None) or [])}
    if not statuses or "insufficient" in statuses:
        return None  # 拒答 / 证据不足
    reason = str(getattr(bundle, "terminal_reason", "") or "")
    if reason == "local_answer":
        return "local" if statuses == {"confirmed"} else None
    if reason in {"AI_GENERATED", "web_evidence_confirmed"}:
        # 联网条目额外护栏：至少 1 条可信来源，否则不把自媒体内容冻结进缓存
        if not any((s.get("level") or "") in _CACHE_TRUSTED_LEVELS for s in sources):
            return None
        return "web"
    return None


class EvidenceAwareRunner:
    def __init__(
        self,
        local_runner: QaRunner,
        pipeline: EvidencePipeline,
        shadow: object | None = None,
        semantic_cache: object | None = None,
    ) -> None:
        self.local_runner = local_runner
        self.pipeline = pipeline
        # Phase 2 影子对比（2026-09-17）：None = 关闭。只记录，不参与回答。
        self.shadow = shadow
        self._shadow_tasks: set[asyncio.Task] = set()
        # 语义缓存（2026-09-18）：None = 关闭；生产由 main 注入共享单例
        self._semantic_cache = semantic_cache

    def _cache_final(self, request: QaRunRequest, bundle: AnswerBundle) -> None:
        """把**最终展示的答案**写进语义缓存（一切异常都吞掉，绝不影响回答）。"""
        if self._semantic_cache is None:
            return
        if _CACHE_TIME_SENSITIVE.search(request.question or ""):
            return  # 时效问句：宁可每次重查，也不端出过期缓存
        kind = _cache_kind(bundle)
        if kind is None:
            return
        namespace = "demo" if request.principal.auth_mode == "demo" else "production"
        hashes = list(getattr(bundle, "cache_source_hashes", None) or []) if kind == "local" else []
        try:
            self._semantic_cache.store(
                request.question, bundle.markdown, namespace,
                source_hashes=hashes,
                structured=list(getattr(bundle, "structured", None) or []),
                sources=list(getattr(bundle, "sources", None) or []),
                kind=kind,
                limitations=list(getattr(bundle, "limitations", None) or []),
            )
        except Exception:  # noqa: BLE001
            pass

    def _shadow_compare(self, request: QaRunRequest, web: AnswerBundle, latency: float) -> None:
        """把这一次线上答案（方案 A）交给影子对比后台记录方案 B。

        只有"确实走了智能搜索生成、且有原始 references"才记——本地答案没有 A/B 可比性。
        **任何异常都吞掉**：影子链路绝不能影响用户拿到的回答。
        """
        if self.shadow is None:
            return
        references = list(getattr(web, "web_references", None) or [])
        if not references:
            return
        shadow = self.shadow

        async def _guarded() -> None:
            # 包一层：影子链路任何异常都在任务内部消化，不留"Task exception was never
            # retrieved"的噪声，也绝不冒到回答路径上。
            try:
                await shadow.compare(
                    question=request.question,
                    a_answer=web.markdown or "",
                    a_references=references,
                    a_latency=latency,
                    a_limitations=list(web.limitations or []),
                    namespace="demo" if request.principal.auth_mode == "demo" else "production",
                    source="production",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"影子对比任务失败: {type(exc).__name__}: {exc}")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的事件循环（同步上下文/单测）→ 直接不记。
            # ⚠️ 必须**先**取循环再建协程，否则 create_task 失败会把协程丢成未 await。
            return
        task = loop.create_task(_guarded(), name=f"shadow-compare:{request.run_id}")
        self._shadow_tasks.add(task)
        task.add_done_callback(self._shadow_tasks.discard)

    async def run(self, request: QaRunRequest) -> AnswerBundle:
        # 2026-09-18：先让内层决定答案（本地/联网/合并），拿到**最终** bundle 再写缓存
        bundle = await self._run_inner(request)
        self._cache_final(request, bundle)
        return bundle

    async def _run_inner(self, request: QaRunRequest) -> AnswerBundle:
        # 闲聊入口快路径：短问候句不进入联网证据链（模板回应，毫秒级）
        if is_chitchat_query(request.question):
            return chitchat_reply()
        if request.effective_mode == "local":
            return await self.local_runner.run(request)
        if request.effective_mode == "web":
            return await self.pipeline.answer(
                request.question,
                profile=request.principal.profile,
                on_stage=request.emit_stage,
                # 2026-09-18：强制联网重答与自动兜底共用同一套流式
                on_delta=getattr(request, "emit_delta", None),
            )

        # ── 本地优先（2026-09-16 改）──
        # 原先「时效词强制联网」（并行预起 + 末尾 needs_web 再压一次）会把本地官方内容
        # 顶掉：实测「最新的转专业政策是什么」本地 claim=confirmed、来源含
        # official_primary，却被换成了百度智能搜索生成的一段**天津科技大学**内容。
        # 现在：**本地答得出来就用本地**，联网只在本地区答不出时兜底。
        # 「纯联网」不再是可选模式，只保留为显式后门（effective_mode == "web"，
        # 由回答下方的「强制联网重答」触发）。
        world_query = _is_world_query(request.question)
        local = await self.local_runner.run(request)
        # 2026-09-18：**联网**缓存命中直接复用 —— 它当初就是"核实过能答"的答案，且 TTL
        # 仅 30 分钟；不短路的话，cache 命中的 claims 是 generated → local_ready 为 False
        # → 又跑一遍联网，缓存等于白存（实测 6.2s 重跑并写出重复条目）。
        # local 类命中不短路：它可能是"未收录"的半成品，仍需判定器决定要不要联网补。
        if getattr(local, "cache_kind", "") == "web":
            return local
        # 未登录 + 问句需要个人数据/校园工具 → 联网无从补足，本地的「请登录」才是权威答案。
        # 否则 local_ready 判 False（"请登录"类 claim 天然是 insufficient）会把这份好答案
        # 顶掉，换成网页泛泛科普（实测引到了极客公园/钛媒体）。2026-09-16。
        if (
            not request.principal.is_authenticated
            # 带时效词的问题（"最新/现行/目前…"）**照旧联网核验**：本地可能过期。
            # 否则"最新政策对我的影响"这类问句会被误拦（2026-09-16 自查发现）。
            and not _HARD_CURRENT_TERMS.search(request.question)
            and _needs_personal_data(request.question, local.markdown or "")
        ):
            note = "未登录：你的课程、成绩与培养方案需登录统一身份认证后读取。"
            if note not in local.limitations:
                local.limitations.append(note)
            return local
        # 本地可答判定：全部 claim 均 confirmed 即可（无论 kind）。
        local_ready = bool(local.claims) and all(
            claim.get("status") == "confirmed" for claim in local.claims
        )
        # 工具结果是校园实时系统直接返回的数据，比网页更权威且天然“最新”，
        # 不应被时效词送去联网核对。
        has_tool_source = any(
            (source.get("level") or "") in {"tool_result", "tool_cache"}
            for source in local.sources
        )
        # 本地答得出来 → 直接用本地（时效问句也一样：本地官方内容优先于联网）。
        # 但"答了个没有"不算答出来：那种情况正是该联网的时候（2026-09-16 修）。
        # 时效问句用本地内容时如实标注，并指向「强制联网重答」这个出口。
        if local_ready and await _local_answered(local, request.question):
            if _CURRENT_TERMS.search(request.question):
                note = ("以上依据本地知识库，可能不是最新；如需最新可点回答下方的"
                        "「强制联网重答」。")
                if note not in local.limitations:
                    local.limitations.append(note)
            return local
        # 世界知识通道（本地未命中 + 非校内通用常识 → LLM 直接答，跳过联网）。
        # 单向双保险：只有 `_is_world_query` 已判为真才会问判定器，且它**只能**把
        # "世界知识"改成"联网"；返回 False/None（不需要联网 / 判定器不可用）都保持原判。
        if world_query and not await asyncio.to_thread(_llm_judge_needs_web, request.question):
            return _world_answer(request.question)

        # 本地答不出 → 串行联网兜底（不再预起：预起会在本地可答时白烧一次联网调用）
        _web_started = asyncio.get_running_loop().time()
        # 2026-09-18：本地只答了一半的复合问题，联网时要把本地已确认内容带进去当基础，
        # 否则联网合成会把本地那半段整段覆盖掉（实测：教秘信息丢失）
        local_hint = _local_merge_hint(local)
        web = await self.pipeline.answer(
            request.question,
            profile=request.principal.profile,
            on_stage=request.emit_stage,
            rounds_limit=1,
            # 2026-09-18：联网合成也真流式（首字从"整篇生成完"提前到首个 token）
            on_delta=getattr(request, "emit_delta", None),
            local_hint=local_hint,
        )
        self._shadow_compare(
            request, web, asyncio.get_running_loop().time() - _web_started
        )
        current = bool(_CURRENT_TERMS.search(request.question))
        # 联网证据不足时回退本地回答，不再丢弃已命中的本地结果。
        # 时效性问题且本地也未确认时，保留诚实拒答（不回退可能过期的数据）。
        fallback_eligible = (
            web.terminal_reason in {"EVIDENCE_INSUFFICIENT", "CRAWL_BLOCKED"}
            # cache_hit 同样是"本地已有的好答案"（语义缓存只存最终展示过的答案）；
            # 漏掉它会把缓存的答案丢掉、端出 88 字固定拒答（2026-09-18 实测）
            and local.terminal_reason in {"local_answer", "cache_hit"}
            and bool(local.markdown.strip())
            and (has_tool_source or local_ready or not current)
        )
        if fallback_eligible:
            if has_tool_source:
                warning = "联网证据不足，已回退校园数据工具结果。"
            elif current:
                warning = "联网证据不足，以下为本地知识库回答（该问题可能涉及时效，请以官方最新信息为准）。"
            else:
                warning = "联网证据不足，已回退本地知识库回答。"
            if warning not in local.limitations:
                local.limitations.append(warning)
            return local
        if local_hint:
            # 合并披露：正文里既有本地已确认内容也有联网内容，必须让用户看得见
            note = ("本答同时包含本地知识库已确认内容与联网检索内容；其中本地部分"
                    "以本校官方文件与综合教务系统为准。")
            if note not in web.limitations:
                web.limitations.append(note)
        # B5｜冲突口径：本地另有**本校官方**材料涉及该问题时，联网结论若与之冲突以本地为准。
        # （本地优先已保证"本地答得出来就不联网"，能走到这里说明本地未 confirmed，
        #   但仍可能召回了官方文档——此时必须给出以本地为准的口径。）
        if any((s.get("level") or "") == "official_primary"
               for s in (getattr(local, "sources", None) or [])):
            note = ("注意：本地知识库中另有本校官方材料涉及该问题；上文若与之冲突，"
                    "请以本校官方文件与综合教务系统为准。")
            if note not in web.limitations:
                web.limitations.append(note)
        return web

    async def close(self) -> None:
        close = getattr(self.local_runner, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        await self.pipeline.close()
