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

from agents.tool_registry import _build_tool_registry
from agents.qa.intents import intent_hint
from agents.qa.state import QaState
from knowledge.intent_classifier import classify
from utils.llm_client import create_llm
from utils.logger import get_logger

log = get_logger("xiaowo.qa.nodes")

MAX_ROUNDS = 4

# 需要 student_id 的个人数据工具（act 层兜底注入学号，防 LLM 漏传导致查空）
_PERSONAL_TOOLS = frozenset({
    "query_schedule", "query_daily_schedule", "query_grade", "calc_gpa",
    "query_exam", "query_course_selection", "query_program",
    "add_event", "get_day_view", "get_week_view", "check_conflict", "import_schedule",
})


# ── 选课推荐参数兜底 ────────────────────────────────


def _load_taken_courses(student_id: str) -> list[str]:
    """从本地成绩表读取已修课程名（供推荐排除已修课程）"""
    if not student_id:
        return []
    try:
        import sqlite3
        from config import DATABASE_PATH
        conn = sqlite3.connect(str(DATABASE_PATH), timeout=10)
        try:
            rows = conn.execute(
                "SELECT DISTINCT course_name FROM student_grades WHERE student_id = ?",
                (student_id,)).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]
    except Exception as e:
        log.warning(f"读取已修课程失败，推荐将不排除已修: {e}")
        return []


def _load_gpa(student_id: str) -> float | None:
    """从本地成绩表计算 4.3 制 GPA（供推荐自动画像；无数据返回 None）"""
    if not student_id:
        return None
    try:
        import sqlite3
        from config import DATABASE_PATH
        from utils.gpa_calculator import calculate_gpa
        conn = sqlite3.connect(str(DATABASE_PATH), timeout=10)
        try:
            rows = conn.execute(
                "SELECT credits, grade_point FROM student_grades WHERE student_id = ?",
                (student_id,)).fetchall()
        finally:
            conn.close()
        if not rows:
            return None
        grades = [{"credits": r[0], "grade_point": r[1]} for r in rows]
        return calculate_gpa(grades)["gpa"]
    except Exception as e:
        log.warning(f"计算 GPA 失败，推荐将不按 GPA 画像: {e}")
        return None


def _enrich_recommend_args(args: dict, state: QaState, sid: str) -> None:
    """recommend_courses 参数兜底：补齐专业/年级/已修课程/学年号。

    LLM 决策常只传兴趣漏传身份，导致方案定位失败退化成全量评分乱推；
    此处从登录用户画像与本地成绩表补齐，保证命中培养方案分组推荐。"""
    profile = dict(args.get("profile") or {})
    up = state.get("user_profile") or {}
    _v = up.get("major")
    if _v and not profile.get("major") and not args.get("major"):
        profile["major"] = _v
    _g = up.get("grade")
    if _g and not profile.get("grade") and not args.get("grade"):
        profile["grade"] = _g
    if not profile.get("taken_courses") and not args.get("taken_courses") and sid:
        taken = _load_taken_courses(sid)
        if taken:
            profile["taken_courses"] = taken
    # 个性化 v1：注入 GPA（由本地成绩表计算），供 recommend_courses 自动推断画像
    if not profile.get("gpa") and not args.get("gpa") and sid:
        gpa = _load_gpa(sid)
        if gpa is not None:
            profile["gpa"] = gpa
    if not profile.get("current_year_index") and not args.get("current_year_index"):
        from tools.advisor_tools import _infer_current_year_index
        yi = _infer_current_year_index(profile.get("grade"))
        if yi:
            profile["current_year_index"] = yi
    args["profile"] = profile


# 个人方案树 per-student 缓存（进程内，避免 QA 循环内多次经 CAS 重复拉取阻塞数秒）
_PERSONAL_TREE_CACHE: dict[str, dict] = {}


def _load_personal_tree(student_id: str | None = None):
    """从 CAS 客户端拉取个人方案树（登录态注入，测试模式已替换为备份数据）。

    带 per-student 缓存：命中直接返回；拉取成功后校验归属（cas_client.student_id
    与当前学生一致，防进程级共享 CAS 客户端在多用户下串数据），不一致返回 None 且不缓存。"""
    if not student_id:
        return None
    cached = _PERSONAL_TREE_CACHE.get(student_id)
    if cached is not None:
        return cached
    try:
        from services.service_container import ServiceContainer
        sc = ServiceContainer()
        if not sc.has_cas():
            return None
        tree = sc.cas_client.get_my_program_tree()
        if isinstance(tree, dict) and "error" in tree:
            return None
        if sc.cas_client.student_id != student_id:
            return None
        _PERSONAL_TREE_CACHE[student_id] = tree
        return tree
    except Exception:
        return None


def _enrich_program_args(args: dict, state: QaState, sid: str, include_taken: bool = False) -> None:
    """培养方案工具参数兜底：补齐专业/年级/已修课程/个人方案树。

    get_program_progress 缺 taken_courses 时会把已修课程误判为必修缺口（"已修0"），
    get_my_program/plan_semester 缺 personal_tree 时退化成全量库方案；
    此处统一从登录画像、本地成绩表与 CAS 方案树补齐。

    include_taken=True 时才注入 taken_courses：仅 get_program_progress 的工具签名
    接受该参数，统一注入会令 get_my_program / plan_semester（extra=forbid）抛 ValidationError。"""
    up = state.get("user_profile") or {}
    _v = up.get("major")
    if _v and not args.get("major"):
        args["major"] = _v
    if not args.get("grade"):
        args["grade"] = up.get("grade") or ""
    if include_taken and not args.get("taken_courses") and sid:
        taken = _load_taken_courses(sid)
        if taken:
            args["taken_courses"] = taken
    if not args.get("personal_tree"):
        tree = _load_personal_tree(sid)
        if tree is not None:
            args["personal_tree"] = tree

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
    "get_my_program(培养方案-我的方案, 参数 major/grade, 个人方案树自动注入), get_program_progress(培养进度, 参数 major/grade, 已修课程自动注入), "
    "plan_semester(学期规划, 参数 major/grade/year_index, 个人方案树自动注入), "
    "collect_preferences(收集选课偏好), recommend_courses(课程推荐, 参数可传 profile={\"major\",\"grade\",\"interests\",\"preference_type\",\"gpa\"} 或顶层 major/grade/interests/preference/keywords/gpa), "
    "compare_courses(课程对比, 参数 course_a/course_b), analyze_teacher(教师评价/课程老师对比, 参数 teacher_name 或 course), "
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
学生信息（登录用户才有；个人数据工具必须携带 student_id）:
{student_info}
对话历史（最近对话，理解"我的/刚才/之前/那个"等指代，仅供参考）:
{chat_history}
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
10. 选课推荐：已有画像（专业/兴趣/偏好）或问题中含偏好线索时直接调用 recommend_courses；用户没提供任何偏好信息（无画像且问题中无专业/兴趣/年级线索）时先 clarify 追问或 collect_preferences 收集，不要用默认画像硬推。已登录用户即使未说偏好，系统已按其 GPA 自动采用画像（tool_summary 的 profile_note 会注明），可直接推荐并在回答中一句话说明画像依据
11. "XX课哪个老师好/哪个老师教得好"类问题用 analyze_teacher(course="课程名")，"XX老师怎么样"用 analyze_teacher(teacher_name="教师名")
12. 工具执行失败若为参数格式错误（validation error），必须用正确参数格式重试一次，不得声称工具不可用或跳过
13. 调用课程相关工具（recommend_courses / analyze_teacher）时，args 中的课程名关键词先解析为规范形式：补全常见简称（"数分"→"数学分析"、"线代"→"线性代数"、"概统"→"概率论与数理统计"），班型编号直接连写在课程名后（如"数学分析B1"），不要凭空添加括号
14. 工具结果含 ambiguity=true 时：decision=clarify，clarify_text 引用 candidates 中的课程名/学院/评论样本量信息反问用户选择哪个班型（例如"您指的是数学分析(B1)（数学科学学院）还是数学分析(B2)？"）；禁止自行替用户做选择
15. 用户已对上一轮 clarify 追问给出明确选择后，允许使用更精确的参数重新调用之前调用过的工具（规则 9 的例外情形）
16. 个人数据工具（query_grade/calc_gpa/query_schedule/query_daily_schedule/query_exam/query_course_selection/query_program/add_event/get_day_view/get_week_view/check_conflict/import_schedule）调用时，args 必须携带 student_id（取自已提供的学生信息），不得省略

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

# 个人信息问答（快速通道，稳定模板回答，避免 LLM 顺着误分类意图编造数据）
_PERSONAL_QA = [
    (r"大几|几年级|哪个年级|什么年级", "grade"),
    (r"什么专业|哪个专业", "major"),
    (r"我?(叫|是)?谁|叫什么名字", "name"),
    (r"学号(是|为)?(多少|什么)?", "id"),
]


_PERSONAL_QA_ANSWER = {
    "grade": "根据你的学籍信息，你是{name}（{id}），{grade}，{major}专业。",
    "major": "你的专业是{major}（{grade}，{name}）。",
    "name": "你是{name}（学号{id}），{major}专业{grade}。",
    "id": "你的学号是{id}（{name}，{major}专业{grade}）。",
}


def _is_personal_qa(query: str) -> str | None:
    """个人信息问答识别：返回命中的字段名；未命中返回 None。
    要求问题为第一人称问自己，避免误伤“他大几”等问别人。"""
    q = (query or "").strip()
    if "我们" in q or "我校" in q or "他" in q or "她" in q:
        return None
    if not re.search(r"(^|[^一-鿿])我|自己", q):
        return None
    if len(q) > 16:  # 过长的复合问题不走模板，交给 LLM
        return None
    for pat, field in _PERSONAL_QA:
        if re.search(pat, q):
            return field
    return None


def _personal_qa_answer(field: str, state: QaState) -> str:
    """个人信息模板回答（从登录画像取值，未登录说明）"""
    up = state.get("user_profile") or {}
    sid = state.get("student_id") or ""
    name = up.get("name") or ""
    major = up.get("major") or ""
    grade = str(up.get("grade") or "")
    if grade and not re.search(r"大[一二三四五六]", grade):
        from tools.advisor_tools import _infer_current_year_index
        yi = _infer_current_year_index(grade, selection=False)
        if yi and 1 <= yi <= 6:
            grade = f"{grade}（大{'一二三四五六'[yi - 1]}）"
    if not (name or sid or major or grade):
        return "你还没有登录，登录后我可以告诉你你的学籍信息哦～"
    return _PERSONAL_QA_ANSWER[field].format(name=name or "同学", id=sid or "未绑定学号",
                                            major=major or "未知专业", grade=grade or "未知年级")


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
    # 个人信息问答快速通道（稳定模板，避免 LLM 顺着误分类意图编造数据）
    personal_field = _is_personal_qa(query)
    if personal_field:
        return {"decision": "compose", "personal_qa": personal_field, "tool_calls": [],
                "thought_log": [{"round": rounds, "decision": "compose", "reason": f"个人信息问答({personal_field})，模板回答"}]}

    candidates = state.get("candidates") or []
    results = state.get("tool_results") or []
    done_tools = {r.get("tool") for r in results if r.get("status") == "done"}
    candidate_note = f"共 {len(candidates)} 条" if candidates else "无匹配"

    try:
        llm = create_llm(temperature=0.0)
        # 注意：推理类模型（deepseek-v4-flash；此前 deepseek-chat 亦被平台路由至 v4-flash-ascend）
        # 对 system-only + 严格 JSON 指令会返回空，必须带 human 消息
        prompt = ChatPromptTemplate.from_messages([
            ("system", THINK_PROMPT),
            ("human", "请做出决策"),
        ])
        response = (prompt | llm).invoke({
            "tools": _TOOL_LIST,
            "query": query,
            "student_info": _build_student_info(state),
            "chat_history": _build_chat_history(state.get("chat_history") or []),
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


def _build_student_info(state: QaState) -> str:
    """学生信息摘要（供 think/compose 决策与回答时识别用户身份）"""
    sid = state.get("student_id") or ""
    profile = state.get("user_profile") or {}
    parts = [f"学号: {sid}"] if sid else []
    if profile.get("name"):
        parts.append(f"姓名: {profile.get('name')}")
    if profile.get("major"):
        parts.append(f"专业: {profile.get('major')}")
    if profile.get("grade"):
        grade = str(profile.get("grade"))
        # 年级换算：2025级 → 2025级（大二），避免 LLM 误读入学年份为当前年级
        if not re.search(r"大[一二三四五六]", grade):
            from tools.advisor_tools import _infer_current_year_index
            yi = _infer_current_year_index(grade, selection=False)
            if yi and 1 <= yi <= 6:
                grade = f"{grade}（大{'一二三四五六'[yi - 1]}）"
        parts.append(f"年级: {grade}")
    return "；".join(parts) if parts else "（未登录，无个人数据）"


def _build_chat_history(history: list[dict], max_items: int = 20) -> str:
    """对话历史摘要（最近 N 条，供多轮指代理解）"""
    if not history:
        return "（无）"
    lines = []
    for m in history[-max_items:]:
        role = "用户" if m.get("role") == "user" else "小蜗"
        lines.append(f"{role}: {str(m.get('content', ''))[:200]}")
    return "\n".join(lines)


def _plan_advisor(query: str, user_profile: dict) -> list[dict]:
    """选课推荐：对比 > 教师分析 > 课程推荐"""
    pair = _extract_course_pair(query) if any(k in query for k in ("对比", "比较")) else None
    if pair:
        return [{"tool": "compare_courses", "args": {"course_a": pair[0], "course_b": pair[1]}}]

    teacher = _extract_teacher(query)
    if teacher:
        return [{"tool": "analyze_teacher", "args": {"teacher_name": teacher}}]

    # "XX课哪个老师好/选哪个老师" → 按课程查老师对比
    m = re.search(r"(.{2,24}?)(?:哪个老师|哪位老师|老师教得好|老师怎么样|哪个老师好)", query)
    if m:
        cname = m.group(1).strip("，。,.！!？? \t")
        return [{"tool": "analyze_teacher", "args": {"course": cname}}]

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
    """从查询与用户信息中尽力提取推荐偏好。

    无信息时留空、绝不填造假画像（历史版本硬编码"计算机科学/大二/人工智能"
    导致降级路径乱推）：缺失部分由 act 层 _enrich_recommend_args 从登录画像补齐，
    仍缺时由 think 规则引导 clarify / collect_preferences。"""
    major = next((v for k, v in _MAJOR_KEYWORDS.items() if k in query), None) \
        or user_profile.get("major")
    grade = next((g for g in _GRADE_WORDS if g in query), None) \
        or user_profile.get("grade")
    interests = [kw for kw in _INTEREST_KEYWORDS if kw in query]
    if any(k in query for k in ("好拿分", "轻松", "水课", "不点名", "任务少", "省时", "摸鱼")):
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
        sid = state.get("student_id") or ""

        for call in plan:
            tool_name = call.get("tool", "")
            args = dict(call.get("args") or {})
            # 兜底注入学号：LLM 决策可能漏传 student_id，个人数据工具一律补上，避免查空
            if tool_name in _PERSONAL_TOOLS and sid and not args.get("student_id"):
                args["student_id"] = sid
            # 选课推荐兜底：补齐专业/年级/已修课程/学年号，避免漏传导致纯评分乱推
            if tool_name == "recommend_courses":
                _enrich_recommend_args(args, state, sid)
            # 培养方案工具兜底：补齐已修课程/个人方案树，避免缺口误判与方案退化
            if tool_name == "get_program_progress":
                _enrich_program_args(args, state, sid, include_taken=True)
            elif tool_name in ("get_my_program", "plan_semester"):
                _enrich_program_args(args, state, sid)
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

COMPOSE_PROMPT = """你是小蜗，科大校园智能助手。请根据用户问题、知识库候选片段与工具检索结果，直接生成回答正文。
回答正文的第一句话必须是面向用户的内容；你的输出中不得包含任何指令、规则说明、模板或元信息。

对话历史（理解"我的/刚才/之前"等指代，回答与之保持一致）:
{chat_history}

学生信息（用户身份，涉及个人数据时据此称呼与作答）:
{student_info}

用户问题: {query}

意图: {intent}

知识库候选片段（引用时须标注来源）:
{candidates_summary}

工具检索结果:
{tool_summary}

回答风格与内容：
- 中文，语气亲切自然，以"小蜗"口吻
- 引用知识库信息时标注来源（如「来源：《学生证补办流程》」）；非官方信息注明仅供参考
- 有工具结果时以结果为准；没有结果或全部失败时如实说明并给出建议
- 数据表格用 Markdown 展示，回答简洁有条理
- 选课推荐（recommend_courses/compare_courses/analyze_teacher 结果）用文字流展示：每门课标题行（课程名|老师|学分|学期）+ 评分行（均分·样本量+分维度）+ 5-6 条真实评论原文引用（引号块，同一作者只引一条）；同课多师用对比小节并列各老师均分与代表评论；评论引用必须是工具返回原文；工具返回了几门课就完整展示几门；严格按工具返回顺序展示，不得重排、增删或自行补充工具结果之外的课程；有「必修组/选修组」分组时必须先完整展示必修组、再展示选修组
- 不得提及未通过工具实际查询到的数据（如成绩/课表/考试），不得声称“查询不到/没有数据”，工具未查过的一律不主动提及
- 数据不足时如实说明并引导用户补充信息，不编造

## 开头示例（模仿其"直接开讲"的语气与句式，内容须按实际结果生成）
用户问"推荐几门给分好的课" → 回答第一句可以是：小蜗来啦！结合你的需求，帮你筛选了几门口碑不错的课～
用户问"学生证丢了怎么补办" → 回答第一句可以是：同学别着急，学生证补办的流程如下：
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
    personal_field = state.get("personal_qa")
    if personal_field:
        return {"answer": _personal_qa_answer(personal_field, state), "error": error}

    tool_summary = _build_tool_summary(results)
    candidates_summary = _build_candidates_summary(candidates)

    try:
        llm = create_llm(temperature=0.3)
        prompt = ChatPromptTemplate.from_messages([
            ("system", COMPOSE_PROMPT),
            ("human", "请直接输出回答正文，第一句必须是面向用户的内容。"),
        ])
        response = (prompt | llm).invoke({
            "query": query,
            "intent": intent,
            "chat_history": _build_chat_history(state.get("chat_history") or []),
            "student_info": _build_student_info(state),
            "candidates_summary": candidates_summary,
            "tool_summary": tool_summary,
        })
        return {"answer": _strip_rule_prefix(response.content), "error": error}
    except Exception as e:
        log.warning(f"QA 综合回答 LLM 失败，降级为结果格式化: {e}")
        return {"answer": _fallback_answer(results, candidates), "error": error or f"LLM 不可用: {e}"}


# 规则特征词：LLM 偶发把 system 指令续写在回答开头，用于识别并剥离
_RULE_KEYWORDS = ("规则", "要求", "输出", "回答", "禁止", "不得", "必须", "为准", "注明",
                  "标注", "来源", "引用", "署名", "结尾", "开头", "模板", "元信息",
                  "提示", "格式", "展示", "编号", "参考")
# 强规则词（单行规则演绎识别，避免误伤正常首行如"办理流程如下："）
_RULE_KEYWORDS_STRONG = ("结尾", "为准", "不得", "禁止", "必须", "须以", "引导", "总结",
                         "提示", "注明", "标注", "署名", "模板", "元信息", "编号", "参考",
                         "官方渠道", "官方来源", "官网")


def _strip_rule_prefix(text: str) -> str:
    """剥离开头混入的规则续写段与模型脏前缀（如 "smart_toy | " 对话痕迹）。"""
    if not text:
        return text
    # 模式0: 模型输出残留的英文 token + 分隔符脏前缀（如 "smart_toy | 小蜗来啦…" 或 "smart_toy\n\n小蜗来啦…"，分隔符可为竖线或换行、可重复）
    m = re.match(r"^[a-z_][a-z_]{1,19}(?:\s*(?:[|｜]|\n)\s*)+(?=[\u4e00-\u9fff\d])", text)
    if m:
        text = text[m.end():].strip()
    # 模式1: 编号规则行
    m = re.match(r"^(\s*\d+[.、．]\s*[^\n]{0,160}\n){1,2}\s*(?:-{3,}\s*\n*)*", text)
    if m and any(k in m.group(0) for k in _RULE_KEYWORDS):
        rest = text[m.end():].strip()
        return rest if rest else text
    # 模式2: 单行规则演绎（短首行 + 强规则词 + 非冒号结尾 + 其后有正文）
    first_end = text.find("\n")
    if first_end == -1:
        return text
    first = text[:first_end].strip()
    if (0 < len(first) <= 60 and not first.endswith(":") and not first.endswith("：")
            and any(k in first for k in _RULE_KEYWORDS_STRONG)):
        rest = text[first_end:].strip("\n ")
        if rest:
            return rest
    return text


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
        elif tool == "recommend_courses" and isinstance(res.get("recommendations"), list):
            groups = res.get("groups") or {}
            req = groups.get("required") or []
            elec = groups.get("elective") or []
            lines.append(f"[{tool}] 共返回 {len(res['recommendations'])} 门课（候选 {res.get('total_candidates')} 门）:")

            def _dump(items, prefix=""):
                for item in items:
                    t_names = "、".join(x["name"] for x in item.get("teachers", [])[:3]) or "未知"
                    hint = item.get("program_hint") or {}
                    if hint:
                        hint_txt = (f"｜方案:{hint.get('program', '')[:20]}/"
                                    f"方案学期{hint.get('term', '?')}/{hint.get('required', '')}")
                    else:
                        hint_txt = ""
                    # terms 是评课库历史开课学期（供参考），方案学期以 program_hint.term 为准
                    terms_txt = "/".join(item.get("terms") or []) or "未知"
                    credit = item.get("credit")
                    credit_txt = f"{credit}学分" if credit else ""
                    lines.append(f"- {item.get('name', '?')}（{credit_txt}） | {t_names} | {item.get('rating_avg')}分·{item.get('rate_count')}条"
                                 f" | 近3次开课 {terms_txt}{hint_txt} | 评论{len(item.get('top_reviews') or [])}条")
                    for rv in (item.get("top_reviews") or [])[:6]:
                        lines.append(f"  > “{rv.get('content', '')[:100]}”——{rv.get('author', '')}({rv.get('term', '')})")

            if req:
                lines.append(f"【必修组·共{len(req)}门，培养方案要求，回答时必须置前展示】")
                _dump(req)
            if elec:
                lines.append(f"【选修组·共{len(elec)}门，方案内选修，按评分排序，回答时置于必修组之后】")
                _dump(elec)
            if not req and not elec:
                _dump(res["recommendations"])
            note = res.get("profile_note") or {}
            if note:
                note_txt = f"画像: {note.get('name','')}——{note.get('desc','')}"
                if note.get("auto") and note.get("gpa") is not None:
                    note_txt += f"（用户未指定偏好，按 GPA {note['gpa']} 自动采用；回答时可用一句话说明）"
                lines.append(note_txt)
            # 选课季语义提示：方案学期“2秋”指大二上学期，避免 LLM 把评课库历史开课学期当“下学期”
            try:
                from tools.advisor_tools import _infer_next_selection_term
                term = _infer_next_selection_term()
            except Exception:
                term = ""
            if term:
                lines.append(f"说明：下一选课学期为 {term}（面向9月开学的新学年）；方案学期「2秋」指大二上学期（以此类推），"
                             f"不要用近3次开课学期代替方案学期。")
        elif tool == "analyze_teacher" and res.get("teachers") and "course" in res:
            lines.append(f"[{tool}] 课程「{res['course']}」共 {len(res['teachers'])} 位老师（均分 {res.get('rating_avg')}·{res.get('rate_count')}条）:")
            for t in res["teachers"]:
                lines.append(f"- {t['name']} | {t['rating_avg']}分·{t['rate_count']}条 | 维度 {t.get('dims_mode', {})}")
            lines.append(f"  评论样本（每条已标注老师, 引用时必须与老师对应, 不得编造）:")
            for rv in (res.get("reviews_sample") or [])[:6]:
                tname = rv.get("teacher") or "未知老师"
                lines.append(f"  > [{tname}] “{rv.get('content', '')[:100]}”——{rv.get('author', '')}({rv.get('term', '')})")
        elif tool == "query_grade" and isinstance(res.get("grades"), list):
            grades = res["grades"]
            lines.append(f"[{tool}] 共 {len(grades)} 门成绩（{res.get('source', '')}）:")
            for g in grades[:60]:
                lines.append(f"- {g.get('semester', '')} {g.get('course_name', '?')} "
                             f"{g.get('credits', '')}学分 成绩{g.get('score', '')} 绩点{g.get('grade_point', '')}")
            if len(grades) > 60:
                lines.append(f"  ... 其余 {len(grades) - 60} 门略")
        elif tool == "calc_gpa" and isinstance(res.get("details"), list):
            lines.append(f"[{tool}] 总GPA {res.get('gpa')}（{res.get('semester')}，{res.get('source', '')}），"
                         f"总学分 {res.get('total_credits')}")
            details = res["details"]
            lines.append(f"  明细 {len(details)} 门:")
            for g in details[:60]:
                lines.append(f"- {g.get('semester', '')} {g.get('course_name', '?')} "
                             f"{g.get('credits', '')}学分 成绩{g.get('score', '')} 绩点{g.get('grade_point', '')}")
        elif tool in ("query_schedule", "query_daily_schedule") and isinstance(res.get("courses"), list):
            courses = res["courses"]
            lines.append(f"[{tool}] 共 {len(courses)} 门课（{res.get('source', '')}，{res.get('semester', '')}）:")
            for c in courses[:80]:
                lines.append(f"- {c.get('course_name', '?')} {c.get('teacher', '')} "
                             f"{c.get('time', '')} {c.get('location', '')}")
        elif tool == "query_course_selection" and isinstance(res.get("selections"), list):
            sels = res["selections"]
            lines.append(f"[{tool}] 共 {len(sels)} 门已选课程:")
            for s in sels[:80]:
                lines.append(f"- {s.get('course_name', '?')} {s.get('teacher', '')} "
                             f"{s.get('credits', '')}学分 {s.get('status', '')}")
        elif tool == "query_exam" and isinstance(res.get("exams"), list):
            exams = res["exams"]
            lines.append(f"[{tool}] 共 {len(exams)} 场考试（{res.get('source', '')}）:")
            for e in exams[:60]:
                lines.append(f"- {e.get('course', '?')} {e.get('date', '')} {e.get('time', '')} "
                             f"{e.get('location', '')} {e.get('type', '')}")
        elif tool == "get_program_progress":
            lines.append(f"[{tool}] {res.get('name', '')} 必修已修 {res.get('required_taken')}/"
                         f"{res.get('required_total')} 门，学分 {res.get('credits_taken')}/"
                         f"{res.get('credits_required')}（{res.get('percent')}%）")
            rem = res.get("required_remaining") or []
            lines.append(f"  必修缺口 {len(rem)} 门:")
            for c in rem[:80]:
                lines.append(f"- {c.get('name', '?')} {c.get('credit', '')}学分 "
                             f"{c.get('term', '')} [{c.get('category', '')}]")
            mp = res.get("modules_progress") or []
            if mp:
                lines.append("  模块进度: " + "、".join(
                    f"{m['category']} {m['taken']}/{m['total']}" for m in mp))
        elif tool == "get_my_program" and isinstance(res.get("courses"), list):
            courses = res["courses"]
            lines.append(f"[{tool}] {res.get('name', '')}（{res.get('grade', '')}）共 {len(courses)} 门课程:")
            for c in courses[:80]:
                lines.append(f"- {c.get('name', '?')} {c.get('code', '')} {c.get('credit', '')}学分 "
                             f"{c.get('required', '')} {c.get('term', '')} [{c.get('category', '')}]")
        elif tool == "plan_semester" and isinstance(res.get("terms"), list):
            terms = res["terms"]
            lines.append(f"[{tool}] 第 {res.get('year_index')} 学年规划，总学分 {res.get('total_credits')}:")
            for t in terms:
                lines.append(f"- {t['term']} 学期 {len(t['courses'])} 门:")
                for c in t["courses"][:60]:
                    lines.append(f"  * {c.get('name', '?')} {c.get('credit', '')}学分 "
                                 f"{c.get('required', '')} [{c.get('category', '')}]")
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
