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
    "check_course_conflict", "evaluate_selection_pressure", "query_activities",
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


def _enrich_add_event_args(args: dict, state: QaState) -> None:
    """add_event 参数兜底：时间缺失或非 ISO（如 LLM 原样传"明天下午3点"）时，
    从原始问题用 time_parser 解析补齐/纠正。

    轮1 实测（2026-08-15）：首调缺 start_time/end_time 报 validation error；
    2026-08-22 实测：LLM 传了非 ISO 原文，add_event 报"时间格式无效"且规则 12
    重试仍原样——故按格式校验而非仅判缺失。"""
    query = state.get("query") or ""
    try:
        from utils.time_parser import enrich_event_time_args
        if enrich_event_time_args(args, query):
            log.info(f"add_event 时间兜底生效: {args.get('start_time')} ~ {args.get('end_time')}")
    except Exception as e:
        log.warning(f"add_event 时间解析兜底失败: {e}")


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

def _ecosystem_tool_fragment() -> str:
    """生态工具清单片段（动态，加载失败返回空串不影响静态清单）。"""
    try:
        from tools.ecosystem import ecosystem_specs
        parts = []
        for s in ecosystem_specs():
            props = (s.get("params_schema") or {}).get("properties") or {}
            req = (s.get("params_schema") or {}).get("required") or []
            arg_hint = ", ".join(
                f"{k}{'必填' if k in req else '可选'}" for k in props) or "无参数"
            parts.append(f"{s['name']}({s['display_name']}·第三方工具, 参数: {arg_hint}, {s['description']})")
        return (", " + ", ".join(parts)) if parts else ""
    except Exception:  # noqa: BLE001
        return ""


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
    "check_conflict(日程冲突), import_schedule(导入课表), "
    "check_course_conflict(选课冲突检测, 参数 course_names 可选, 检测已选课程间的节次/周次冲突), "
    "evaluate_selection_pressure(退补选压力评估, 参数 add_courses/drop_courses/credit_cap 可选, 学分上限与时间负荷评估), "
    "render_link(校园官方入口跳转, 参数 scene=场景描述如 退课/缴费/评教, 返回官方系统名称+URL), "
    "query_activities(青春科大第二课堂活动查询, 参数 keyword=关键词 category=分类 time_window=即将截止/周末/本周 limit=条数, 实时返回报名中活动)"
    + _ecosystem_tool_fragment()
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
    # top_k=12：多义字段/组合问法（如"退学联系谁"需学籍文档+教秘名单组合，
    # "住宿费/学费/贷款"等多篇争夺）时保证含答案文档进入候选（2026-08-16 补录）
    candidates, found = [], False
    try:
        from knowledge.vector_store import FAQVectorStore
        res = FAQVectorStore().search(query, top_k=12)
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
11. "XX课哪个老师好/哪个老师教得好"类问题用 analyze_teacher(course="课程名")，"XX老师怎么样"用 analyze_teacher(teacher_name="教师名")；"XX老师在XX课怎么样/XX老师的XX课评价"（同时含老师与课程）用 analyze_teacher(teacher_name="教师名", course="课程名") 双参数，聚焦该老师在该课程的评价，不要只用 teacher_name 全量返回
12. 工具执行失败若为参数格式错误（validation error），必须用正确参数格式重试一次，不得声称工具不可用或跳过
13. 调用课程相关工具（recommend_courses / analyze_teacher）时，args 中的课程名关键词先解析为规范形式：补全常见简称（"数分"→"数学分析"、"线代"→"线性代数"、"概统"→"概率论与数理统计"），班型编号直接连写在课程名后（如"数学分析B1"），不要凭空添加括号
14. 工具结果含 ambiguity=true 时：decision=clarify，clarify_text 引用 candidates 中的课程名/学院/评论样本量信息反问用户选择哪个班型（例如"您指的是数学分析(B1)（数学科学学院）还是数学分析(B2)？"）；禁止自行替用户做选择
15. 用户已对上一轮 clarify 追问给出明确选择后，允许使用更精确的参数重新调用之前调用过的工具（规则 9 的例外情形）
16. 个人数据工具（query_grade/calc_gpa/query_schedule/query_daily_schedule/query_exam/query_course_selection/query_program/add_event/get_day_view/get_week_view/check_conflict/import_schedule）调用时，args 必须携带 student_id（取自已提供的学生信息），不得省略；调用 query_daily_schedule/get_day_view 时，问句中的"周X/明天/后天"等日期词必须解析后作为 date/date_str 传参，不得缺省为今天
17. 选课冲突/退补选：问"冲突/撞课/时间重/课表重"→ check_course_conflict（course_names 可选，只检测指定课程）；问"退选/退课/补选/学分超/学分压力/选太多/要退哪门"→ evaluate_selection_pressure（add_courses/drop_courses 可选，模拟加退课）；周次不重叠不算冲突，周次未知按重叠保守判定，一切以工具返回如实转述，不得臆造排课时间
18. 复合问题（如"先查我的成绩再推荐课程"）每轮只调用一个工具，后续轮次继续调用其他工具完成，最多 {max_rounds} 轮；选择工具前先核对工具清单与规则 1-17，确保工具与问题意图匹配
19. 联系人类事务问法（"退学/休学/转专业/缓考/选课异常等事务联系谁/找谁/联系方式"）：若当前候选片段中未含具体联系人（姓名/电话/邮箱/办公地点），须调用 search_faq(query="<学生所属学院名> 教学秘书 联系方式") 或改写检索词补一次知识库检索。注意：检索词必须含**学院名**（取自 student_info 中的专业/学院，如"人工智能 学院 教学秘书 联系方式"），仅搜"教学秘书联系方式"或"退学"这类通用词无法命中学院名单块（名单块需学院名做锚点）；拿到具体联系信息后再合成
20. 生态工具（名称以 eco: 开头，第三方同学提供）：结果转述时必须标注提供者署名与"仅供参考"，不得与官方数据混写；工具失败时如实说明失败原因，不编造结果；用户明确要求测试/使用该第三方工具时才调用
21. 强操作类诉求（改变官方系统状态的操作：选课/退课/换班/评教提交/缴费/活动报名等，小蜗无权代办）→ 调 render_link(scene=场景) 给出官方入口 URL，并主动提供小蜗能做的辅助（如退课前 evaluate_selection_pressure 模拟、选课前 check_course_conflict 冲突检测）；**URL 只能来自 render_link 返回或知识库来源，禁止自行生成/拼造任何 URL**；render_link 返回 found=false 时如实说不知道入口
22. 活动/第二课堂问句（"有什么活动/讲座/志愿可以报名"、"最近有什么活动"、"周末有什么活动"、"XX月X日有什么活动"）→ 调 query_activities（可带 keyword/category/time_window 过滤），如实转述返回的活动（名称/主办方/时间/报名截止），不得编造活动或修改时间；活动报名本身是强操作，追问报名入口时按规则 21 给 render_link

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


# ── 确定性工具路由（LLM 之前，保证高置信场景工具调用正确）──────────────
# 适用条件：意图 + 关键词双命中。这些场景工具映射唯一、参数由 act 层兜底、
# 无澄清需求，不经 LLM 直接调用，杜绝 LLM 选错工具的概率性错误。
_DIRECT_ROUTE_KEYWORDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "选课冲突": ("check_course_conflict", ("冲突", "撞课", "时间重", "重了", "时间挤")),
    "退补选评估": ("evaluate_selection_pressure",
                   ("退选", "退课", "补选", "退补选", "学分超", "学分够", "学分压力",
                    "选太多", "退掉", "退哪门", "压力")),
}


def _teacher_course_route(state: QaState) -> dict | None:
    """教师开课查询确定性路由："XX老师有哪些课/教什么课/开过哪些课/上什么课"。

    2026-08-16 新增：embedding 意图分类常把这类问句归入「课程搜索」（因"有哪些课"
    是课程搜索示例句），导致 think 调用 search_courses(教师名) 查不到数据。
    此处直接映射 analyze_teacher(teacher_name=...)，不依赖意图分类。"""
    query = state.get("query") or ""
    rounds = state.get("rounds") or 0
    intent = state.get("intent") or ""
    if intent == "课程搜索" and not re.search(r"老师|教授|导师", query):
        return None
    if not re.search(r"(有哪些课|教什么课|开过哪些课|上什么课|上哪些课|教哪些课|开什么课|主讲什么|带什么课|授什么课)", query):
        return None
    teacher = _extract_teacher(query)
    if not teacher:
        return None
    tool = "analyze_teacher"
    results = state.get("tool_results") or []
    if any(r.get("tool") == tool and r.get("status") == "done" for r in results):
        return {
            "decision": "compose",
            "tool_calls": [],
            "thought_log": (state.get("thought_log") or []) + [{
                "round": rounds + 1, "decision": "compose",
                "reason": f"教师开课查询工具 {tool} 已有结果，直接合成",
            }],
        }
    return {
        "decision": "call_tool",
        "tool_calls": [{"tool": tool, "args": {"teacher_name": teacher}}],
        "thought_log": (state.get("thought_log") or []) + [{
            "round": rounds + 1, "decision": "call_tool",
            "reason": f"教师开课查询({intent})→{tool}(teacher_name={teacher})",
        }],
    }


def _is_dayview_query(query: str) -> bool:
    """周X/明天 + 安排/课/日程的疑问句（如"周四晚上有什么安排"）→ 直连日课表。"""
    has_day = any(d in query for d in (
        "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天", "明天", "后天"))
    if not has_day:
        return False
    has_topic = any(k in query for k in ("安排", "课", "日程"))
    has_ask = any(w in query for w in ("什么", "哪些", "几门", "几节", "？", "?", "吗", "嘛"))
    return has_topic and has_ask


def _direct_tool_route(state: QaState) -> dict | None:
    """高置信意图 → 确定性工具路由；条件不满足返回 None（交 LLM 决策）。

    轮1 实测修复（2026-08-15）：路由未检查工具是否已有结果，导致工具被
    重复调用直至轮次上限（Q11/Q12 各调 4 次）。此处补 done 检查：路由工具
    已有成功结果时转 compose，不再重复调用。
    2026-08-16 加固：允许「选课推荐」意图在命中强冲突/退补选关键词时同样
    直连对应工具，避免 embedding 对"冲突"类问句分类在「选课冲突/选课推荐」
    之间漂移时路由失效（如"推荐几门课会不会和我课表冲突"）。"""
    intent = state.get("intent") or ""
    query = state.get("query") or ""
    rounds = state.get("rounds") or 0
    # 意图归并：选课域父类意图统一可为路由触发上下文
    intent_set = {intent}
    if intent == "选课推荐":
        intent_set = {"选课冲突", "退补选评估"}
    entry = None
    if intent_set & {"选课冲突", "退补选评估"}:
        for k, (t, kws) in _DIRECT_ROUTE_KEYWORDS.items():
            if k in intent_set and any(kw in query for kw in kws):
                entry = (t, kws)
                break
    if not entry:
        # 2026-08-22 增补：周X/明天+安排/课 问句确定性直连日课表。think 偶发把
        # 该类问句漂移到 get_day_view 且不解析日期（缺省今天），导致"周四晚上
        # 有什么安排"答成"今天(周六)无事件"；query_daily_schedule 直接读课表
        # 并输出精确时钟，是该类问句的主诉求。
        if _is_dayview_query(query):
            day_tool = "query_daily_schedule"
            results = state.get("tool_results") or []
            if any(r.get("tool") == day_tool and r.get("status") == "done" for r in results):
                return {
                    "decision": "compose",
                    "tool_calls": [],
                    "thought_log": (state.get("thought_log") or []) + [{
                        "round": rounds + 1, "decision": "compose",
                        "reason": f"确定性路由工具 {day_tool} 已有结果，直接合成",
                    }],
                }
            args = {}
            date_phrase = _extract_date_phrase(query)
            if date_phrase:
                args["date"] = date_phrase
            return {
                "decision": "call_tool",
                "tool_calls": [{"tool": day_tool, "args": args}],
                "thought_log": (state.get("thought_log") or []) + [{
                    "round": rounds + 1, "decision": "call_tool",
                    "reason": f"确定性路由(日课表)→{day_tool}(date={args.get('date', '')})",
                }],
            }
        return None
    tool, keywords = entry
    if not any(k in query for k in keywords):
        return None
    # 防重复：路由工具已有成功结果 → 直接合成（与 LLM 路径 done_tools 同口径）
    results = state.get("tool_results") or []
    if any(r.get("tool") == tool and r.get("status") == "done" for r in results):
        return {
            "decision": "compose",
            "tool_calls": [],
            "thought_log": (state.get("thought_log") or []) + [{
                "round": rounds + 1, "decision": "compose",
                "reason": f"确定性路由工具 {tool} 已有结果，直接合成",
            }],
        }
    return {
        "decision": "call_tool",
        "tool_calls": [{"tool": tool, "args": {}}],
        "thought_log": (state.get("thought_log") or []) + [{
            "round": rounds + 1, "decision": "call_tool",
            "reason": f"确定性路由({intent})→{tool}",
        }],
    }


_REGISTRY_CACHE: frozenset | None = None


def _valid_tools() -> frozenset:
    """注册表工具名集合（懒加载缓存）"""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = frozenset(_build_tool_registry())
    return _REGISTRY_CACHE


def _check_tool_choice(tool: str, done_tools: set) -> str:
    """校验 LLM 选择的工具：''=空 'done'=已有结果 'unknown'=不在注册表 'ok'=可用"""
    if not tool:
        return "empty"
    if tool in done_tools:
        return "done"
    if tool not in _valid_tools():
        return "unknown"
    return "ok"


def think(state: QaState) -> dict:
    """LLM 自主决策下一步动作；LLM 不可用时降级为确定性规则"""
    query = state.get("query", "")
    intent = state.get("intent") or "知识问答"
    rounds = state.get("rounds") or 0

    # P3-2 熔断：本轮问答内 LLM 已失败过 → 不再尝试 LLM，直接走确定性规则，
    # 避免多轮每轮都等超时（断网时 think ≤4 轮叠加分钟级假死）
    if state.get("llm_down"):
        log.info("think: LLM 已熔断（本轮此前失败），直接确定性规则")
        fb = _think_fallback(state)
        fb["llm_down"] = True
        return fb

    # 敏感词/闲聊快速通道（不依赖 LLM，稳定拒绝与回应）
    # 守卫：intent 命中敏感类且问题含敏感词才拒绝，避免 embedding 误分类
    # （如"补考成绩怎么记载"被归入敏感类）把正常教务问题误拒绝
    if intent == "敏感拒绝" and any(w in query for w in _SENSITIVE_WORDS):
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

    # 确定性工具路由（意图+关键词双条件，保证正确调用）
    # 教师开课查询确定性路由（"XX老师有哪些课"→analyze_teacher，先于冲突路由）
    tcr = _teacher_course_route(state)
    if tcr is not None:
        log.info(f"think[{tcr['decision']}] → 教师开课查询路由")
        return tcr

    direct = _direct_tool_route(state)
    if direct is not None:
        if direct["decision"] == "call_tool":
            log.info(f"think[{rounds + 1}] → call_tool (确定性路由 {direct['tool_calls'][0]['tool']})")
        else:
            log.info(f"think[{rounds + 1}] → compose (确定性路由工具已有结果)")
        return direct

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
            verdict = _check_tool_choice(tool, done_tools)
            if verdict == "empty":
                update["decision"] = "compose"
            elif verdict == "done":
                # 防重复：该工具已有成功结果，不得再调，转合成（防死循环）
                log.info(f"think: 工具 {tool} 已有结果，禁止重复调用，转合成")
                update["decision"] = "compose"
                update["tool_calls"] = []
                update["thought_log"] = (state.get("thought_log") or []) + [{
                    "round": rounds + 1, "decision": "compose", "reason": f"已有{tool}结果，禁止重复调用",
                }]
            elif verdict == "unknown":
                # LLM 选择了不存在的工具：降级到确定性计划，不让 act 报错
                log.warning(f"think: LLM 选择了未知工具 {tool}，降级为确定性规则")
                return _think_fallback(state)
            else:
                update["tool_calls"] = [{"tool": tool, "args": data.get("args") or {}}]

        log.info(f"think[{rounds + 1}] → {update.get('decision')} ({data.get('reason', '')[:50]})")
        return update
    except Exception as e:
        log.warning(f"think LLM 决策失败，降级为确定性规则: {e}")
        from utils.llm_client import mark_llm_down_if_unreachable
        mark_llm_down_if_unreachable(e)  # 仅连接级失败开熔断窗（读超时不动窗）
        fb = _think_fallback(state)
        fb["llm_down"] = True  # 熔断标记：本轮后续轮次不再尝试 LLM
        return fb


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


def _keyword_intent_fix(query: str, intent: str) -> str:
    """P3-2：embedding 意图分类不可用（平台断/误分类）时的关键词兜底。

    只在高置信词命中时覆盖，宁缺毋滥；顺序敏感（导入类先行）。
    """
    if "导入" in query and "课" in query:
        return "日程管理"  # 课表导入日程（import_schedule）
    if any(k in query for k in ("课表", "有哪些课", "什么课", "上课时间", "哪天有课")):
        return "查课表"
    if any(k in query for k in ("日程", "待办", "备忘")):
        return "日程管理"
    if any(k in query for k in ("推荐", "选课建议", "选什么课")):
        return "选课推荐"
    return intent


def _think_fallback(state: QaState) -> dict:
    """确定性规则降级：已有可用结果直接合成；否则按意图选择工具计划"""
    intent = state.get("intent") or "知识问答"
    query = state.get("query", "")
    student_id = state.get("student_id") or ""
    rounds = state.get("rounds") or 0
    intent = _keyword_intent_fix(query, intent)

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
        # 联系人类事务问法：检索词拼接用户画像学院名，确保命中学院教秘名单块
        faq_query = query
        if re.search(r"(联系谁|找谁|联系方式|联系|找谁办)", query):
            major = (state.get("user_profile") or {}).get("major") or ""
            if major:
                faq_query = f"{major} 学院 教学秘书 联系方式"
        plan, decision = [{"tool": "search_faq", "args": {"query": faq_query}}], "call_tool"

    log.info(f"think 降级规则[{intent}]: {[p['tool'] for p in plan]}")
    return {"decision": decision, "tool_calls": plan,
            "thought_log": (state.get("thought_log") or []) + [{
                "round": rounds + 1, "decision": decision, "reason": f"降级规则({intent})",
            }]}


def _build_candidates_summary(candidates: list[dict]) -> str:
    """候选召回片段摘要（含官方来源，供 system 提示中引用官方网址）。
    按 score 降序排列后取前 12 条展示——候选池可能混合首轮检索与 retrieve 追加
    结果（追加顺序不等于相关度），排序保证高相关片段（如学院名单块）不被挤出。"""
    if not candidates:
        return "（无候选片段）"
    ordered = sorted(candidates, key=lambda c: -(c.get("score") or 0))
    lines = []
    for i, c in enumerate(ordered[:12], 1):
        title = c.get("title") or c.get("source") or "未命名"
        content = str(c.get("content", ""))[:220]
        src = c.get("source")
        src_part = f" 来源:{src}" if src else ""
        lines.append(f"[{i}] 《{title}》 score={c.get('score')} is_official={c.get('is_official', True)}{src_part}: {content}")
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
    """选课推荐：冲突/退补选 > 对比 > 教师分析 > 课程推荐"""
    # 选课 H 项：冲突检测 / 退补选压力评估（与 _plan_course 同口径）
    if any(k in query for k in ("冲突", "撞课", "时间重", "重了", "时间挤")):
        return [{"tool": "check_course_conflict", "args": {}}]
    if any(k in query for k in ("退选", "退课", "补选", "退补选", "学分超", "学分够",
                                "学分压力", "选太多", "退掉", "退哪门")):
        return [{"tool": "evaluate_selection_pressure", "args": {}}]

    pair = _extract_course_pair(query) if any(k in query for k in ("对比", "比较")) else None
    if pair:
        return [{"tool": "compare_courses", "args": {"course_a": pair[0], "course_b": pair[1]}}]

    teacher = _extract_teacher(query)
    # 双参数问法："XX老师在XX课怎么样/XX老师的XX课评价" → teacher_name + course 同时提供，
    # 聚焦该老师在该课程的评价（避免教师模式全量返回后摘要截断丢失目标课程）
    m2 = re.search(r"(.{1,12}?(?:老师|老师))\s*(?:的|在|教)?\s*[^，。！？]{0,20}?([^，。！？]{1,24}?)", query)
    if teacher and re.search(r"B[0-9]|班|课|数学分析|线性代数|概率论|物理|化学|英语|微积分|计算", query):
        cm = re.search(r"([\u4e00-\u9fffA-Za-z0-9（）()]{2,24}?)(?:怎么样|评价|如何|好不好|怎么上|教得好|的评价|的表现)", query)
        if cm and "老师" not in cm.group(1):
            return [{"tool": "analyze_teacher", "args": {"teacher_name": teacher, "course": cm.group(1)}}]
        if teacher and re.search(r"的\s*([\u4e00-\u9fffA-Za-z0-9（）()]{2,16}?)", query):
            cm2 = re.search(r"的\s*([\u4e00-\u9fffA-Za-z0-9（）()]{2,16}?)(?:课|班|评价|怎么样|如何)", query)
            if cm2:
                return [{"tool": "analyze_teacher", "args": {"teacher_name": teacher, "course": cm2.group(1)}}]
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

    # 选课 H 项：冲突检测 / 退补选压力评估（节次级精确判断，替代 LLM 目测）
    if any(k in query for k in ("冲突", "撞课", "时间重", "重了", "时间挤")):
        return [{"tool": "check_course_conflict", "args": sid_args}]
    if any(k in query for k in ("退选", "退课", "补选", "退补选", "学分超", "学分够",
                                "学分压力", "选太多", "退掉", "退哪门")):
        return [{"tool": "evaluate_selection_pressure", "args": sid_args}]

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

    if any(k in query for k in ("导入", "同步")) and "课" in query:
        return [{"tool": "import_schedule", "args": args}]
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
            res = FAQVectorStore().search(query, top_k=12)
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
            # 添加日程兜底：LLM 常只传标题漏传时间，从问题用 time_parser 解析补齐
            # （轮1 实测 2026-08-15：首调缺 start_time/end_time 报 validation error）
            if tool_name == "add_event" and sid:
                _enrich_add_event_args(args, state)
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
- 候选片段携带官方来源（candidates_summary 中 "来源:..." 字段，通常是「官方文档标题：https://...」的行）时，在回答末尾附上一行「相关：」并给出可点击的官方网址（把 URL 用 Markdown 链接或原文展示，便于用户跳转核实）。若片段来源列为非官方，则不附链接并注明仅供参考
- 有工具结果时以结果为准；没有结果或全部失败时如实说明并给出建议
- 工具结果含"第三方工具 · XX 提供"（eco: 前缀生态工具）时：回答必须保留该提供者署名并注明"仅供参考"，不得表述为小蜗或官方数据
- 选课结果（query_course_selection）含上课时间与地点时：必须逐项列出并基于数据判断；若两门课上课时间重叠（同一天同一节次），明确指出"疑似时间冲突"并给出退改建议；不得在已有时间数据的情况下声称"无法判断冲突"
- 数据表格用 Markdown 展示，回答简洁有条理
- 选课推荐（recommend_courses/compare_courses/analyze_teacher 结果）用文字流展示：每门课标题行（课程名|老师|学分|学期）+ 评分行（均分·样本量+分维度）+ 5-6 条真实评论原文引用（引号块，同一作者只引一条）；同课多师用对比小节并列各老师均分与代表评论；评论引用必须是工具返回原文；工具返回了几门课就完整展示几门；严格按工具返回顺序展示，不得重排、增删或自行补充工具结果之外的课程；有「必修组/选修组」分组时必须先完整展示必修组、再展示选修组；每门课的方案学期必须如实转述标注（「2秋」= 大二上学期，「3春」= 大三下学期），不得臆造学期，也不得把评课库历史开课学期当作方案学期
- 不得提及未通过工具实际查询到的数据（如成绩/课表/考试），不得声称“查询不到/没有数据”，工具未查过的一律不主动提及
- 必修组课程是培养方案要求：展示顺序必须与工具返回一致，不得重排；不得将必修课表述为「可作备选」「可考虑退」等可选性措辞
- 课程学分、均分、样本量、学期等数值必须取自工具返回结果，不得猜测、修改或补充；工具未提供学分的不得臆造学分
- 先修课要求、成绩满足性、课程容量、开课院系等工具未返回的信息：必须明确说明“暂无该数据”并给出查证途径（如登录综合教务系统核对），严禁根据常识推断用户的已修状态或满足性（不得出现“已修，成绩满足”这类未经工具核实的结论）
- 冲突检测（check_course_conflict）与压力评估（evaluate_selection_pressure）结果必须如实转述：周次不重叠不算冲突；周次未知按重叠保守判定时须注明；时间不全/无排课数据的课程明确说明无法精确检测；学分上限为参考值（默认30），以教务系统为准；不得在工具未提供排课时间的情况下臆造冲突结论
- 绩点、学分、日期、百分比等数值性知识必须以知识库候选片段或工具结果中的官方数值为准；候选片段含对照表/数字时逐条核对后再回答，严禁凭记忆输出或推算数值（如"XX分对应多少绩点"必须按候选片段中的对照表回答）
- 当官方信息含"因专业/因年份/因人群而有差异的多个数值"时（如不同专业学费不同、不同年份缴费标准不同），必须区分适用对象作答，**不得用其中某一个特例数值代表整体**（例如学费应区分"普通本科4800/传播学4500"，不得笼统答"4500"），并在必要时提示不同对象数值以官方为准
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

    if intent == "敏感拒绝" and any(w in query for w in _SENSITIVE_WORDS):
        return {"answer": _sensitive_refusal(), "error": error}
    if intent == "闲聊" and not results and not candidates:
        return {"answer": _chitchat(query), "error": error}
    personal_field = state.get("personal_qa")
    if personal_field:
        return {"answer": _personal_qa_answer(personal_field, state), "error": error}

    tool_summary = _build_tool_summary(results)
    candidates_summary = _build_candidates_summary(candidates)

    # P3-2 熔断：本轮 think 阶段 LLM 已失败 → 合成层不再尝试连接（省一次超时等待）
    if state.get("llm_down"):
        log.info("compose: LLM 已熔断（本轮此前失败），直接输出降级摘要")
        return {"answer": _llm_down_answer(tool_summary, candidates_summary, results, candidates),
                "error": error or "LLM 不可用（熔断降级）"}

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
        answer = _strip_rule_prefix(response.content)
        if not (answer or "").strip():
            log.warning("compose LLM 返回空回答，降级为结果格式化")
            return {"answer": _llm_down_answer(tool_summary, candidates_summary, results, candidates,
                                               note="LLM 返回空回答"),
                    "error": error or "LLM 返回空回答"}
        return {"answer": answer, "error": error}
    except Exception as e:
        log.warning(f"QA 综合回答 LLM 失败，降级为结果格式化: {e}")
        from utils.llm_client import mark_llm_down_if_unreachable
        mark_llm_down_if_unreachable(e)  # 仅连接级失败开熔断窗（读超时不动窗）
        return {"answer": _llm_down_answer(tool_summary, candidates_summary, results, candidates),
                "error": error or f"LLM 不可用: {e}"}


def _llm_down_answer(tool_summary: str, candidates_summary: str,
                     results: list[dict], candidates: list[dict], note: str = "") -> str:
    """P3-2 LLM 不可用时的合成层降级：工具摘要/候选摘要原文直出 + 顶部固定提示。

    优先用 _build_tool_summary/_build_candidates_summary 的定制化摘要（按工具类型
    完整呈现），退化路径与旧 _fallback_answer 一致（无任何结果时如实说明）。
    """
    body = ""
    if tool_summary and tool_summary.strip():
        body = tool_summary.strip()
        if candidates_summary and candidates_summary.strip():
            body += f"\n\n{candidates_summary.strip()}"
    elif candidates_summary and candidates_summary.strip():
        body = candidates_summary.strip()
    else:
        fallback = _fallback_answer(results, candidates)
        if fallback == "抱歉，我暂时无法回答这个问题，请换个说法试试。":
            return fallback
        body = fallback
    head = "⚠️ LLM 服务暂不可用，以下为工具/知识库原始结果（未经整理，仅供参考）："
    if note:
        head += f"（{note}）"
    return f"{head}\n\n{body}"


# 规则特征词：LLM 偶发把 system 指令续写在回答开头，用于识别并剥离
_RULE_KEYWORDS = ("规则", "要求", "输出", "回答", "禁止", "不得", "必须", "为准", "注明",
                  "标注", "来源", "引用", "署名", "结尾", "开头", "模板", "元信息",
                  "提示", "格式", "展示", "编号", "参考")
# 强规则词（单行规则演绎识别，避免误伤正常首行如"办理流程如下："）
_RULE_KEYWORDS_STRONG = ("结尾", "为准", "不得", "禁止", "必须", "须以", "引导", "总结",
                         "提示", "注明", "标注", "署名", "模板", "元信息", "编号", "参考",
                         "官方渠道", "官方来源", "官网")


def _strip_rule_prefix(text: str) -> str:
    """剥离开头混入的规则续写段与模型脏前缀（如 "smart_toy | " 对话痕迹）。

    Phase 2c 加固：模式0 容忍 BOM/零宽空格/空行前置，并按"token+分隔符"单元循环剥离
    （模型偶发多段拼接，如 "smart_toy | smart_toy | 正文"）。
    """
    if not text:
        return text
    # 模式0: 英文 token + 分隔符脏前缀（分隔符可为竖线或换行、可重复；整体可多段、可循环）
    _pat0 = re.compile(
        r"^[\s\u200b\ufeff]*"
        r"(?:[a-z_][a-z_]{1,19}(?:\s*(?:[|｜]|\n)\s*)+)+"
        r"(?=[\u4e00-\u9fff\d])")
    prev = None
    while prev != text:
        prev = text
        m = _pat0.match(text)
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


def _term_zh(term) -> str:
    """方案学期中文名（组 A 修复）：'2秋'→'大二上'、'3春'→'大三下'、'1夏'→'大一暑期'；无法解析返回原文。"""
    t = str(term or "").strip()
    m = re.match(r"^([1-6])([春秋夏])$", t)
    if not m:
        return t or "?"
    zh_n = "一二三四五六"[int(m.group(1)) - 1]
    zh_s = {"秋": "上", "春": "下", "夏": "暑期"}[m.group(2)]
    return f"大{zh_n}{zh_s}"


def _build_tool_summary(results: list[dict]) -> str:
    """将工具结果整理为供 LLM 参考的摘要文本"""

    def _src(res: dict) -> str:
        """source 值 → 中文明确标签（避免 LLM 把 'fallback' 误读成官方来源而编造出处）。"""
        return {
            "real": "数据来源：教务系统实时数据",
            "fallback": "数据来源：本地缓存/模拟数据，仅供参考",
            "local": "数据来源：本地培养方案数据（非教务实时），以综合教务系统为准",
            "locked": "需登录教务系统后获取",
        }.get(res.get("source") or "", res.get("source") or "来源未知")

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
                                    f"方案学期{hint.get('term', '?')}（{_term_zh(hint.get('term'))}）/{hint.get('required', '')}")
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
        elif tool == "analyze_teacher" and res.get("teacher"):
            # 教师模式（teacher_name 或 teacher_name+course）：完整呈现各课程评分，防止
            # 通用兜底 json.dumps[:800] 截断导致 B1/B2 等课程数据丢失（2026-08 修复）
            courses = res.get("courses") or []
            lines.append(f"[{tool}] 教师「{res.get('teacher')}」共 {len(courses)} 门课"
                         f"（综合均分 {res.get('avg_rating')}·{res.get('review_count')}条，评课数据仅供参考）:")
            for c in courses:
                lines.append(f"- {c['name']} | {c['rating_avg']}分·{c['rate_count']}条")
            sample = res.get("reviews_sample") or []
            if sample:
                lines.append(f"  评论样本（{len(sample)} 条, 每条已标注课程与老师, 引用时必须对应）:")
                for rv in sample[:6]:
                    tname = rv.get("teacher") or res.get("teacher") or "未知老师"
                    lines.append(f"  > [{tname}] “{rv.get('content', '')[:100]}”——{rv.get('author', '')}({rv.get('term', '')})")
        elif tool == "query_grade" and isinstance(res.get("grades"), list):
            grades = res["grades"]
            lines.append(f"[{tool}] 共 {len(grades)} 门成绩（{_src(res)}）:")
            for g in grades[:60]:
                lines.append(f"- {g.get('semester', '')} {g.get('course_name', '?')} "
                             f"{g.get('credits', '')}学分 成绩{g.get('score_display', g.get('score', ''))} 绩点{g.get('grade_point', '')}")
            if len(grades) > 60:
                lines.append(f"  ... 其余 {len(grades) - 60} 门略")
        elif tool == "calc_gpa" and isinstance(res.get("details"), list):
            lines.append(f"[{tool}] 总GPA {res.get('gpa')}（{res.get('semester')}，{_src(res)}），"
                         f"总学分 {res.get('total_credits')}")
            details = res["details"]
            lines.append(f"  明细 {len(details)} 门:")
            for g in details[:60]:
                lines.append(f"- {g.get('semester', '')} {g.get('course_name', '?')} "
                             f"{g.get('credits', '')}学分 成绩{g.get('score_display', g.get('score', ''))} 绩点{g.get('grade_point', '')}")
        elif tool in ("query_schedule", "query_daily_schedule") and isinstance(res.get("courses"), list):
            courses = res["courses"]
            lines.append(f"[{tool}] 共 {len(courses)} 门课（{_src(res)}，{res.get('semester', '')}）:")
            if tool == "query_daily_schedule":
                # 某天课表：优先展示换算后的精确时钟（避免"第19:00~19:30节"原始串被误读为节次号）
                for c in courses[:80]:
                    clock = f"{c.get('start_time', '')}~{c.get('end_time', '')}" if c.get("start_time") else "时间未解析"
                    lines.append(f"- {clock} {c.get('course_name', '?')} {c.get('teacher', '')} "
                                 f"{c.get('location', '')}（{c.get('periods', '')} {c.get('weeks', '')}）")
            else:
                for c in courses[:80]:
                    lines.append(f"- {c.get('course_name', '?')} {c.get('teacher', '')} "
                                 f"{c.get('time', '')} {c.get('location', '')}")
        elif tool == "query_course_selection" and isinstance(res.get("selections"), list):
            sels = res["selections"]
            lines.append(f"[{tool}] 共 {len(sels)} 门已选课程（含上课时间与地点，供冲突/压力判断）:")
            for s in sels[:80]:
                lines.append(f"- {s.get('course_name', '?')} {s.get('teacher', '')} "
                             f"{s.get('credits', '')}学分 {s.get('time', '')} {s.get('location', '')} "
                             f"{s.get('status', '')}")
        elif tool == "check_course_conflict" and isinstance(res.get("courses"), list):
            lines.append(f"[{tool}] 共检查 {res.get('total', 0)} 门课（{_src(res)}）: "
                         f"冲突 {res.get('conflict_count', 0)} 处")
            for c in res.get("conflicts") or []:
                wu = "（周次未知，按重叠保守判定）" if c.get("weeks_unknown") else ""
                lines.append(f"- ⚠ {c.get('course_a', '?')} × {c.get('course_b', '?')}："
                             f"{c.get('day', '?')} {c.get('reason', '')}{wu}")
            if res.get("time_incomplete"):
                lines.append(f"- 时间不全（无法精确判定）: {', '.join(res['time_incomplete'])}")
            if res.get("missing"):
                lines.append(f"- 未找到排课数据（评课库/缓存无排课时间，不得臆造）: {', '.join(res['missing'])}")
            if not (res.get("conflicts") or res.get("time_incomplete") or res.get("missing")):
                lines.append("- 已检查课程两两之间无节次/周次冲突")
        elif tool == "evaluate_selection_pressure" and isinstance(res.get("current"), dict):
            cur = res["current"]
            lines.append(f"[{tool}] 当前选课 {cur.get('course_count', 0)} 门 / "
                         f"{cur.get('total_credits', 0)} 学分（上限参考 {cur.get('credit_cap', 30)}，以教务系统为准，{_src(res)}）: "
                         f"冲突 {cur.get('conflict_count', 0)} 处")
            for c in cur.get("conflicts") or []:
                wu = "（周次未知，按重叠保守判定）" if c.get("weeks_unknown") else ""
                lines.append(f"- ⚠ {c.get('course_a', '?')} × {c.get('course_b', '?')}："
                             f"{c.get('day', '?')} {c.get('reason', '')}{wu}")
            daily = cur.get("daily") or {}
            if daily:
                lines.append("- 每日负荷: " + "、".join(
                    f"{d} {v.get('course_count', 0)}门/{v.get('slot_count', 0)}段" for d, v in daily.items()))
            if cur.get("busiest_day"):
                lines.append(f"- 最忙 {cur.get('busiest_day')}")
            if cur.get("time_incomplete"):
                lines.append(f"- 时间不全: {', '.join(cur['time_incomplete'])}")
            after = res.get("after_add_drop")
            if after is not None:
                lines.append(f"- 模拟后（退 {res.get('drops_applied') or '无'}，加 {res.get('adds_pending') or '无'}）: "
                             f"{after.get('course_count', 0)} 门 / {after.get('total_credits', 0)} 学分，"
                             f"冲突 {after.get('conflict_count', 0)} 处")
            for s in res.get("suggestions") or []:
                lines.append(f"- 建议: {s}")
        elif tool == "query_exam" and isinstance(res.get("exams"), list):
            exams = res["exams"]
            lines.append(f"[{tool}] 共 {len(exams)} 场考试（{_src(res)}）:")
            for e in exams[:60]:
                lines.append(f"- {e.get('course', '?')} {e.get('date', '')} {e.get('time', '')} "
                             f"{e.get('location', '')} {e.get('type', '')}")
        elif tool == "get_program_progress":
            lines.append(f"[{tool}] {res.get('name', '')} 必修已修 {res.get('required_taken')}/"
                         f"{res.get('required_total')} 门，学分 {res.get('credits_taken')}/"
                         f"{res.get('credits_required')}（{res.get('percent')}%，{_src(res)}）")
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
            lines.append(f"[{tool}] {res.get('name', '')}（{res.get('grade', '')}）共 {len(courses)} 门课程（{_src(res)}）:")
            for c in courses[:80]:
                lines.append(f"- {c.get('name', '?')} {c.get('code', '')} {c.get('credit', '')}学分 "
                             f"{c.get('required', '')} {c.get('term', '')} [{c.get('category', '')}]")
        elif tool == "plan_semester" and isinstance(res.get("terms"), list):
            terms = res["terms"]
            lines.append(f"[{tool}] 第 {res.get('year_index')} 学年规划，总学分 {res.get('total_credits')}（{_src(res)}）:")
            for t in terms:
                lines.append(f"- {t['term']} 学期 {len(t['courses'])} 门:")
                for c in t["courses"][:60]:
                    lines.append(f"  * {c.get('name', '?')} {c.get('credit', '')}学分 "
                                 f"{c.get('required', '')} [{c.get('category', '')}]")
        elif tool == "query_activities" and isinstance(res.get("activities"), list):
            acts = res["activities"]
            lines.append(f"[{tool}] 报名中活动 {res.get('count')}/{res.get('total_enrolment')} 条"
                         f"（{_src(res)}，拉取于 {res.get('fetched_at', '')}）:")
            for a in acts[:12]:
                dl = f"，报名截止 {a.get('apply_end')}" if a.get("apply_end") else ""
                lines.append(f"- {a.get('name', '?')} | {a.get('organizer', '')} | "
                             f"{a.get('category', '')} | {a.get('start', '')}~{a.get('end', '')}{dl} | "
                             f"人数 {a.get('people_num', 0)} | 工时 {a.get('service_hour', '')}")
            lines.append("  （转述要求：逐条如实呈现名称/主办方/时间/报名截止，不得增删编造；报名入口见 render_link/young.ustc.edu.cn）")
        elif tool == "render_link":
            if res.get("found"):
                lines.append(f"[{tool}] 官方入口：{res.get('name')} {res.get('url')}"
                             f"（{res.get('description', '')}，分类 {res.get('category', '')}）。"
                             f"回答中给出该名称与可点击 URL，说明该操作需在官方系统完成，"
                             f"并可主动提供小蜗辅助（冲突检测/压力评估等）")
            else:
                lines.append(f"[{tool}] {res.get('note', '未找到匹配入口')}——回答时如实说明不知道入口，"
                             f"禁止编造 URL")
        elif tool.startswith("eco:"):
            # P4-1 生态工具通用分支：首行强制署名，字段逐项展开（不截断成 json 串）
            provider, display = "", ""
            try:
                from tools.ecosystem import ecosystem_specs
                for s in ecosystem_specs():
                    if s.get("name") == tool:
                        provider, display = str(s.get("provider", "")), str(s.get("display_name", ""))
                        break
            except Exception:  # noqa: BLE001
                pass
            head = f"[{tool}] {display}（第三方工具 · {provider or '未知提供者'} 提供，仅供参考）:"
            if res.get("error"):
                lines.append(head + f" 执行失败——{res['error']}")
            else:
                lines.append(head)
                for k, v in res.items():
                    if k == "source":
                        continue
                    if isinstance(v, (str, int, float, bool)):
                        lines.append(f"- {k}: {v}")
                    elif isinstance(v, (list, dict)):
                        lines.append(f"- {k}: {json.dumps(v, ensure_ascii=False)[:400]}")
        elif res.get("message"):
            lines.append(f"[{tool}] {res['message']}")
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
