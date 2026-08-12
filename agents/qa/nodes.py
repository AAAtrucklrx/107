"""
小蜗 — QA LangGraph 节点
embedding_parse: 意图分类（embedding 相似度，参考信号）+ 候选召回（混合检索）双通道
think:           LLM 自主决策（clarify/retrieve/call_tool/compose），失败降级确定性规则
act:             执行重检索/工具调用，结果回填
compose:         LLM 综合回答（来源标注/非官方提示/敏感拒绝，失败降级格式化）
"""

from __future__ import annotations

import json
import re

from langchain_core.prompts import ChatPromptTemplate

from agents.executor import _build_tool_registry
from agents.qa.intents import intent_hint
from agents.qa.state import QaState
from knowledge.intent_classifier import classify
from utils.llm_client import create_llm
from utils.logger import get_logger

log = get_logger("xiaowo.qa.nodes")

MAX_ROUNDS = 4

# ── 模块信号 → 意图（仅作软提示，不强制覆盖） ──────────

MODULE_TO_INTENT = {
    "智能问答": "知识问答",
    "课业助手": "查课表",
    "选课顾问": "选课推荐",
    "日程管理": "日程查询",
}

_MODULE_HINT_MIN_SCORE = 0.5  # embedding 分类置信度低于此值才参考模块信号

# ── 可用工具清单（think 决策参考） ────────────────────

_TOOL_LIST = (
    "search_faq(知识库检索), get_faq_categories(知识库分类), "
    "query_schedule(课表), query_daily_schedule(某天课表), find_empty_room(空教室), "
    "query_grade(成绩), calc_gpa(绩点), query_exam(考试安排), "
    "search_courses(课程搜索), get_semester_list(学期列表), "
    "query_course_selection(选课情况), query_program(培养方案), "
    "collect_preferences(收集选课偏好), recommend_courses(课程推荐), "
    "compare_courses(课程对比), analyze_teacher(教师评价), "
    "add_event(添加日程), get_day_view(日视图), get_week_view(周视图), "
    "check_conflict(日程冲突), import_schedule(导入课表)"
)


def embedding_parse(state: QaState) -> dict:
    """双通道：意图分类（参考信号）+ 知识库候选召回"""
    query = state.get("query", "")
    module_signal = state.get("module_signal") or "自动判断"

    result = classify(query)
    top3 = result.get("top3") or []
    intent = result.get("intent", "知识问答")

    # 模块信号仅在分类置信度低时参考（弱信号，不强制）
    hint = MODULE_TO_INTENT.get(module_signal)
    if hint and result.get("method") == "embedding" and top3 and top3[0].get("score", 0) < _MODULE_HINT_MIN_SCORE:
        log.info(f"意图置信度低({top3[0].get('score')})，参考模块信号 {module_signal} → {hint}")
        intent = hint

    # 候选召回（向量+BM25 混合检索）
    candidates, found = [], False
    try:
        from knowledge.vector_store import FAQVectorStore
        res = FAQVectorStore().search(query, top_k=5)
        candidates = res.get("results") or []
        found = res.get("found", False)
    except Exception as e:
        log.warning(f"候选召回失败: {e}")

    log.info(f"意图识别: {intent} (method={result.get('method')}, module={module_signal}, 候选 {len(candidates)} 条)")
    return {"intent": intent, "intent_top3": top3, "candidates": candidates, "candidates_found": found}


# ── think: LLM 自主决策（降级为确定性规则） ────────────

THINK_PROMPT = """你是小蜗的决策引擎。根据用户问题与已有信息，决定下一步动作。只输出 JSON，不要输出其他内容。

## 可用工具
{tools}

## 输入
用户问题: {query}
模块信号（用户可能手动选择了模块，仅供参考，可忽略）: {module_signal}
意图参考（embedding 分类结果，仅供参考，若与真实意图不符必须自行修正）: {intent} —— {intent_hint}
知识库候选召回（{candidate_note}）:
{candidates_summary}
已有工具结果（第 {rounds} 轮，最多 {max_rounds} 轮）:
{tool_summary}

## 决策规则
1. decision=compose：已有候选或工具结果足以回答 → 直接进入合成
2. decision=retrieve：候选不足或答非所问 → 在 query 字段给出改写后的检索词，重新检索知识库
3. decision=call_tool：需要个人数据或动态信息（课表/成绩/GPA/空教室/考试/课程推荐/日程）→ 指定 tool 与 args
4. decision=clarify：问题模糊且没有工具能直接处理 → 在 clarify_text 字段给出追问
5. 敏感请求（作弊/改成绩/代考/抄袭）→ decision=compose，不调用任何工具，合成时礼貌拒绝
6. 闲聊问候（你好/谢谢/你是谁）→ decision=compose，不调用任何工具
7. 意图分类仅供参考，自行判断真实需求并修正
8. 绝不编造数据：工具结果不足时继续 retrieve/call_tool/clarify，不要硬答
9. 禁止重复调用：已有工具结果（tool_summary 中 status=done 的工具）不得再次调用同一工具，应转 compose 或 clarify
10. 选课推荐优先直接调用 recommend_courses（args 用用户画像 profile 或合理默认偏好）；仅在确实缺关键信息时先 collect_preferences，且收集后不得再次收集

## 输出格式（严格 JSON）
{{"decision": "clarify|retrieve|call_tool|compose", "tool": "工具名，call_tool 时必填", "args": {{工具参数}}，"query": "retrieve 时的改写检索词", "reason": "简短理由", "clarify_text": "clarify 时的追问内容"}}"""


_TIME_WORDS = ["今天", "明天", "后天", "昨天", "这周", "下周", "周一", "周二", "周三",
               "周四", "周五", "周六", "周日", "上午", "下午", "晚上"]
_BUILDINGS = ["高新", "一教", "二教", "三教", "四教", "五教"]
_MAJOR_KEYWORDS = {"计算机": "计算机科学", "人工智能": "人工智能", "数学": "数学",
                   "物理": "物理", "生物": "生物", "化学": "化学", "金融": "金融", "经管": "经管"}
_INTEREST_KEYWORDS = ["人工智能", "AI", "机器学习", "深度学习", "编程", "算法", "数学",
                      "英语", "物理", "生物", "化学", "金融", "经管", "文学", "历史", "设计"]
_GRADE_WORDS = ["大一", "大二", "大三", "大四"]


_SENSITIVE_WORDS = ["作弊", "改成绩", "代考", "抄袭", "替考", "考试答案", "舞弊"]

_CHITCHAT_WORDS = ["你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "谢谢", "感谢", "辛苦", "你是谁", "拜拜", "再见"]


def _is_chitchat(query: str) -> bool:
    """闲聊判定：仅当问题短且含问候特征词（避免误判真实问题）"""
    q = (query or "").strip()
    if not q or len(q) > 12:
        return False
    return any(w in q.lower() for w in _CHITCHAT_WORDS)


def think(state: QaState) -> dict:
    """LLM 自主决策下一步动作；LLM 不可用时降级为确定性规则"""
    query = state.get("query", "")
    intent = state.get("intent") or "知识问答"
    rounds = state.get("rounds") or 0

    # 敏感词/闲聊快速通道（不依赖 LLM，稳定拒绝与回应）
    if intent == "敏感拒绝" or any(w in query for w in _SENSITIVE_WORDS):
        return {"decision": "compose", "tool_calls": [],
                "thought_log": [{"round": rounds, "decision": "compose", "reason": "敏感请求，直接礼貌拒绝"}]}
    if intent == "闲聊" and _is_chitchat(query):
        return {"decision": "compose", "tool_calls": [],
                "thought_log": [{"round": rounds, "decision": "compose", "reason": "闲聊问候，直接回应"}]}

    candidates = state.get("candidates") or []
    results = state.get("tool_results") or []
    done_tools = {r.get("tool") for r in results if r.get("status") == "done"}
    candidate_note = f"共 {len(candidates)} 条" if candidates else "无匹配"

    try:
        llm = create_llm(temperature=0.0)
        # 注意：deepseek-chat 对 system-only + 严格 JSON 指令会返回空，必须带 human 消息
        prompt = ChatPromptTemplate.from_messages([
            ("system", THINK_PROMPT),
            ("human", "请做出决策"),
        ])
        response = (prompt | llm).invoke({
            "tools": _TOOL_LIST,
            "query": query,
            "module_signal": state.get("module_signal") or "自动判断",
            "intent": intent,
            "intent_hint": intent_hint(intent),
            "candidate_note": candidate_note,
            "candidates_summary": _build_candidates_summary(candidates),
            "rounds": rounds + 1,
            "max_rounds": MAX_ROUNDS,
            "tool_summary": _build_tool_summary(results),
        })
        data = _parse_json_loose(response.content)
        if not data:
            raise ValueError("无法从 LLM 输出中解析 JSON")

        decision = data.get("decision", "compose")
        if decision not in ("clarify", "retrieve", "call_tool", "compose"):
            decision = "compose"

        update = {
            "decision": decision,
            "retrieve_query": data.get("query", "") if decision == "retrieve" else "",
            "clarify_question": data.get("clarify_text", "") if decision == "clarify" else "",
            "thought_log": (state.get("thought_log") or []) + [{
                "round": rounds + 1, "decision": decision, "reason": data.get("reason", ""),
            }],
        }

        if decision == "call_tool":
            tool = data.get("tool", "")
            if not tool:
                update["decision"] = "compose"
            elif tool in done_tools:
                # 防重复：该工具已有成功结果，不得再调，转合成（防死循环）
                log.info(f"think: 工具 {tool} 已有结果，禁止重复调用，转合成")
                update["decision"] = "compose"
                update["tool_calls"] = []
                update["thought_log"] = (state.get("thought_log") or []) + [{
                    "round": rounds + 1, "decision": "compose", "reason": f"已有{tool}结果，禁止重复调用",
                }]
            else:
                update["tool_calls"] = [{"tool": tool, "args": data.get("args") or {}}]

        log.info(f"think[{rounds + 1}] → {update.get('decision')} ({data.get('reason', '')[:50]})")
        return update
    except Exception as e:
        log.warning(f"think LLM 决策失败，降级为确定性规则: {e}")
        return _think_fallback(state)


def _parse_json_loose(text: str) -> dict | None:
    """宽松 JSON 解析：兼容 ```json 代码块与前后缀文本，失败返回 None"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else text
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _think_fallback(state: QaState) -> dict:
    """确定性规则降级：已有可用结果直接合成；否则按意图选择工具计划"""
    intent = state.get("intent") or "知识问答"
    query = state.get("query", "")
    student_id = state.get("student_id") or ""
    rounds = state.get("rounds") or 0

    # 已有可用工具结果（如 search_faq 命中）则收敛直接合成，避免重复检索
    results = state.get("tool_results") or []
    if any(r.get("status") == "done" and not (r.get("result") or {}).get("error") for r in results):
        log.info("think 降级规则：已有可用工具结果，直接合成")
        return {"decision": "compose", "tool_calls": [],
                "thought_log": (state.get("thought_log") or []) + [{
                    "round": rounds + 1, "decision": "compose", "reason": "降级规则(已有结果)",
                }]}

    if intent == "闲聊":
        plan, decision = [], "compose"
    elif intent == "选课推荐":
        plan, decision = _plan_advisor(query, state.get("user_profile") or {}), "call_tool"
    elif intent == "查课表":
        plan, decision = _plan_course(query, student_id), "call_tool"
    elif intent == "日程管理":
        plan, decision = _plan_schedule(query, student_id), "call_tool"
    else:
        plan, decision = [{"tool": "search_faq", "args": {"query": query}}], "call_tool"

    log.info(f"think 降级规则[{intent}]: {[p['tool'] for p in plan]}")
    return {"decision": decision, "tool_calls": plan,
            "thought_log": (state.get("thought_log") or []) + [{
                "round": rounds + 1, "decision": decision, "reason": f"降级规则({intent})",
            }]}


def _build_candidates_summary(candidates: list[dict]) -> str:
    """候选召回片段摘要"""
    if not candidates:
        return "（无候选片段）"
    lines = []
    for i, c in enumerate(candidates[:5], 1):
        title = c.get("title") or c.get("source") or "未命名"
        content = str(c.get("content", ""))[:220]
        lines.append(f"[{i}] 《{title}》 score={c.get('score')} is_official={c.get('is_official', True)}: {content}")
    return "\n".join(lines)


def _plan_advisor(query: str, user_profile: dict) -> list[dict]:
    """选课推荐：对比 > 教师分析 > 课程推荐"""
    pair = _extract_course_pair(query) if any(k in query for k in ("对比", "比较")) else None
    if pair:
        return [{"tool": "compare_courses", "args": {"course_a": pair[0], "course_b": pair[1]}}]

    teacher = _extract_teacher(query)
    if teacher:
        return [{"tool": "analyze_teacher", "args": {"teacher_name": teacher}}]

    if any(k in query for k in ("推荐", "选修", "通识", "选什么", "什么课")):
        return [{"tool": "recommend_courses", "args": {"profile": _extract_profile(query, user_profile)}}]

    return [{"tool": "search_faq", "args": {"query": query}}]


def _plan_course(query: str, student_id: str) -> list[dict]:
    """课业助手：按关键词优先级匹配单一工具"""
    sid_args = {"student_id": student_id}

    if "空教室" in query or "教室" in query:
        return [{"tool": "find_empty_room",
                 "args": {"building": _extract_building(query), "time_desc": _extract_time_desc(query)}}]
    if "GPA" in query.upper() or "绩点" in query:
        return [{"tool": "calc_gpa", "args": sid_args}]
    if "成绩" in query or "分数" in query:
        return [{"tool": "query_grade", "args": sid_args}]
    if "考试" in query:
        return [{"tool": "query_exam", "args": sid_args}]
    if "选课" in query and any(k in query for k in ("情况", "结果", "选了")):
        return [{"tool": "query_course_selection", "args": sid_args}]
    if "培养方案" in query or "培养计划" in query:
        return [{"tool": "query_program", "args": sid_args}]

    date_phrase = _extract_date_phrase(query)
    if date_phrase:
        return [{"tool": "query_daily_schedule", "args": {"student_id": student_id, "date": date_phrase}}]
    return [{"tool": "query_schedule", "args": sid_args}]


def _plan_schedule(query: str, student_id: str) -> list[dict]:
    """日程管理：添加 > 周视图 > 日视图"""
    args = {"student_id": student_id}

    if any(k in query for k in ("添加", "记一下", "提醒", "安排")):
        title = _extract_event_title(query)
        if title:
            from utils.time_parser import iso_format, parse_natural_time
            parsed = parse_natural_time(query)
            return [{"tool": "add_event", "args": {
                "student_id": student_id,
                "title": title,
                "start_time": iso_format(parsed["date"], parsed["period_start"]),
                "end_time": iso_format(parsed["date"], parsed["period_end"]),
            }}]

    if any(k in query for k in ("这周", "本周", "忙不忙", "一周")):
        return [{"tool": "get_week_view", "args": args}]
    return [{"tool": "get_day_view", "args": args}]


def _extract_course_pair(query: str) -> tuple[str, str] | None:
    """从对比类查询中提取两门课程名（尽力而为）"""
    parts = re.split(r"[和与]|vs|VS", query)
    if len(parts) >= 2:
        a = re.sub(r"^(对比|比较|帮我|一下|请)+", "", parts[0]).strip()
        b = parts[1].strip()
        if a and b:
            return a, b
    return None


def _extract_teacher(query: str) -> str | None:
    m = re.search(r"([\u4e00-\u9fff]{2,4})(?:老师|教授)", query)
    return m.group(1) if m else None


def _extract_profile(query: str, user_profile: dict) -> dict:
    """从查询与用户信息中尽力提取推荐偏好"""
    major = next((v for k, v in _MAJOR_KEYWORDS.items() if k in query), None) \
        or user_profile.get("major") or "计算机科学"
    grade = next((g for g in _GRADE_WORDS if g in query), None) \
        or user_profile.get("grade") or "大二"
    interests = [kw for kw in _INTEREST_KEYWORDS if kw in query] or ["人工智能"]
    if any(k in query for k in ("好拿分", "轻松", "水课")):
        pref = "easy_grade"
    elif any(k in query for k in ("学到东西", "硬核", "挑战")):
        pref = "learn_hard"
    else:
        pref = "balanced"
    return {"major": major, "grade": grade, "interests": interests,
            "preference_type": pref, "max_results": 5}


def _extract_building(query: str) -> str:
    for b in _BUILDINGS:
        if b in query:
            return b
    return "三教"


def _extract_time_desc(query: str) -> str:
    found = [w for w in _TIME_WORDS if w in query]
    return "".join(found) or "今天下午"


def _extract_date_phrase(query: str) -> str | None:
    for w in ("下周一", "下周二", "下周三", "下周四", "下周五", "下周六", "下周日",
              "今天", "明天", "后天", "昨天", "周一", "周二", "周三", "周四", "周五", "周六", "周日"):
        if w in query:
            return w
    return None


_TITLE_NOISE = ["添加", "记一下", "提醒我", "提醒", "安排", "帮我", "请", "一个", "日程", "事件", "开"]


def _extract_event_title(query: str) -> str:
    title = query
    for w in _TITLE_NOISE:
        title = title.replace(w, "")
    title = re.sub(r"(下?周[一二三四五六日天]|今天|明天|后天|昨天|上午|下午|晚上|\d+点|\d{1,2}:\d{2})", "", title)
    return title.strip("，。,.！!？? ") or "新日程"


# ── act: 按决策执行（重检索 / 工具调用） ───────────────────

def act(state: QaState) -> dict:
    """按 think 决策执行：retrieve 重检索知识库 / call_tool 调用工具，rounds+1"""
    decision = state.get("decision") or "compose"
    rounds = (state.get("rounds") or 0) + 1
    update: dict = {"rounds": rounds}

    if decision == "retrieve":
        query = state.get("retrieve_query") or state.get("query", "")
        candidates = list(state.get("candidates") or [])
        try:
            from knowledge.vector_store import FAQVectorStore
            res = FAQVectorStore().search(query, top_k=5)
            seen = {(c.get("id") or c.get("chunk_id")) for c in candidates}
            added = 0
            for c in res.get("results") or []:
                cid = c.get("id") or c.get("chunk_id")
                if cid not in seen:
                    candidates.append(c)
                    seen.add(cid)
                    added += 1
            update["candidates"] = candidates
            update["candidates_found"] = res.get("found", False) or bool(state.get("candidates_found"))
            log.info(f"act[retrieve] '{query}' → 新增 {added} 条候选，共 {len(candidates)} 条")
        except Exception as e:
            log.warning(f"act 重检索失败: {e}")
        return update

    if decision == "call_tool":
        registry = _build_tool_registry()
        plan = state.get("tool_calls") or []
        results: list[dict] = []

        for call in plan:
            tool_name = call.get("tool", "")
            args = call.get("args") or {}
            func = registry.get(tool_name)
            if func is None:
                log.error(f"未知工具: {tool_name}")
                results.append({"tool": tool_name, "status": "error",
                                "result": {"error": f"未知工具: {tool_name}，可用工具: {_TOOL_LIST}"}})
                continue
            try:
                result = func.invoke(args) if hasattr(func, "invoke") else func(**args)
                results.append({"tool": tool_name, "status": "done",
                                "result": result if isinstance(result, dict) else {"output": str(result)}})
                log.info(f"工具 {tool_name} 执行成功")
            except Exception as e:
                log.error(f"工具 {tool_name} 执行失败: {e}")
                results.append({"tool": tool_name, "status": "error",
                                "result": {"error": f"工具 {tool_name} 执行失败: {str(e)}"}})

        update["tool_results"] = (state.get("tool_results") or []) + results
        update["tool_calls"] = []  # 本轮计划已消费，清空避免残留
        return update

    # compose/clarify 不应进入 act，防御性返回
    return update


# ── compose: 综合回答 ───────────────────────────────────

COMPOSE_PROMPT = """你是小蜗，科大校园智能助手。
根据用户问题、知识库候选片段与工具检索结果，生成完整、准确、友好的回答。

## 用户问题
{query}

## 意图
{intent}

## 知识库候选片段（每条含来源文档，引用时须标注来源）
{candidates_summary}

## 工具检索结果
{tool_summary}

## 回答要求
1. 直接回答用户的问题，不要重复用户的提问
2. 使用中文，语气亲切自然
3. 引用知识库候选片段时标注来源（如「来源：《学生证补办流程》」）；片段 is_official=False 时注明"以下信息来自非官方来源，仅供参考"
4. 有工具结果时以结果为准；没有结果或全部失败时如实说明并给出建议
5. 结果中 source 字段不是 "real" 时，在回答开头注明数据来源（如"教务系统暂时不可用，以下为本地缓存/模拟数据，仅供参考"），不得把降级数据当作实时数据呈现
6. 数据表格用 Markdown 展示；回答简洁有条理
7. 未调用任何工具（如问候闲聊）时，直接友好回应即可
8. 绝不编造：候选与工具结果都不足以回答时，如实说明并引导用户补充信息
"""


def compose(state: QaState) -> dict:
    """生成最终回答；敏感请求固定拒绝、闲聊直接回应；LLM 不可用时降级格式化"""
    query = state.get("query", "")
    intent = state.get("intent", "知识问答")
    results = state.get("tool_results") or []
    candidates = state.get("candidates") or []
    error = state.get("error") or ""

    if intent == "敏感拒绝":
        return {"answer": _sensitive_refusal(), "error": error}
    if intent == "闲聊" and not results and not candidates:
        return {"answer": _chitchat(query), "error": error}

    tool_summary = _build_tool_summary(results)
    candidates_summary = _build_candidates_summary(candidates)

    try:
        llm = create_llm(temperature=0.3)
        prompt = ChatPromptTemplate.from_messages([("system", COMPOSE_PROMPT)])
        response = (prompt | llm).invoke({
            "query": query,
            "intent": intent,
            "candidates_summary": candidates_summary,
            "tool_summary": tool_summary,
        })
        return {"answer": response.content, "error": error}
    except Exception as e:
        log.warning(f"QA 综合回答 LLM 失败，降级为结果格式化: {e}")
        return {"answer": _fallback_answer(results, candidates), "error": error or f"LLM 不可用: {e}"}


def _chitchat(query: str) -> str:
    q = query or ""
    if any(k in q for k in ("你好", "您好", "嗨", "hi", "hello", "在吗")):
        return "你好呀！我是小蜗，科大校园智能助手，有什么可以帮你？"
    if any(k in q for k in ("谢谢", "感谢", "辛苦")):
        return "不客气，有需要随时找我！"
    if any(k in q for k in ("你是谁", "你是什么", "介绍一下你")):
        return "我是小蜗，科大校园智能助手。可以帮你解答校园问题、查询课表成绩、推荐课程、管理日程。"
    return "我在的，有什么可以帮你？"


def _sensitive_refusal() -> str:
    """敏感请求固定拒绝文案（不依赖 LLM，稳定拦截）"""
    return ("抱歉，这类问题小蜗无法提供帮助。考试与学业请遵守学校纪律，"
            "如有学业困难可以咨询辅导员或任课老师，学校也提供学业辅导与心理咨询等支持渠道。")


def _build_tool_summary(results: list[dict]) -> str:
    """将工具结果整理为供 LLM 参考的摘要文本"""
    lines = []
    for r in results:
        tool = r.get("tool", "")
        res = r.get("result") or {}
        if r.get("status") == "error":
            lines.append(f"[{tool}] 执行失败: {res.get('error', '未知错误')}")
        elif res.get("error"):
            lines.append(f"[{tool}] {res['error']}")
        elif isinstance(res.get("results"), list) and res.get("found"):
            lines.append(f"[{tool}] 找到 {len(res['results'])} 条结果:")
            for item in res["results"][:3]:
                lines.append(f"- {str(item.get('content', ''))[:300]}")
        else:
            lines.append(f"[{tool}] {json.dumps(res, ensure_ascii=False)[:800]}")
    return "\n".join(lines) if lines else "（无工具结果）"


def _fallback_answer(results: list[dict], candidates: list[dict]) -> str:
    """LLM 不可用时的降级回答：工具结果优先，其次展示候选片段（含来源）"""
    done = [r for r in results if r.get("status") == "done"]
    if not done and not candidates:
        return "抱歉，我暂时无法回答这个问题，请换个说法试试。"

    lines = []
    for r in done:
        res = r.get("result") or {}
        if res.get("error"):
            lines.append(f"提示：{res['error']}")
        elif isinstance(res.get("results"), list) and res.get("found"):
            top = res["results"][0]
            content = str(top.get("content", ""))
            src = top.get("source")
            lines.append(content + (f"\n（来源：{src}）" if src else ""))
        elif "output" in res:
            lines.append(str(res["output"]))
        elif res:
            parts = [f"{k}: {v}" for k, v in res.items()
                     if isinstance(v, (str, int, float)) and k not in ("source", "message")]
            lines.append("\n".join(parts) if parts else str(res))

    if not done:
        for c in candidates[:3]:
            content = str(c.get("content", ""))[:300]
            src = c.get("source") or c.get("title") or "知识库"
            official = c.get("is_official", True)
            note = "" if official else "，非官方来源，仅供参考"
            lines.append(f"{content}\n（来源：{src}{note}）")
    return "\n\n".join(lines)
