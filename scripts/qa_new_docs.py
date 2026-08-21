# -*- coding: utf-8 -*-
"""综合验证新增知识库文档：run_qa 直连 + 官方 URL 透出。
复用 scripts/qa_new_docs.py 的用例，前台运行。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DATABASE_PATH, SCHEMA_PATH
from services.service_container import ServiceContainer
from services.session_ctx import set_student, reset_student

def setup():
    sc = ServiceContainer()
    sc.init_database(DATABASE_PATH, SCHEMA_PATH)
    tok = set_student("PB25111691")
    c = sc.cas_client
    c._logged_in = True
    c._student_id = "PB25111691"
    import tools.course_tools as ct
    ct.set_offline_mode(True)
    reset_student(tok)

setup()
from agents.qa.graph import run_qa

CASES = [
    ("四六级怎么报名？", "cet-bm", "neea.edu.cn"),
    ("英语六级什么时候考试？", "6月13日", "teach.ustc.edu.cn"),
    ("本科普通专业一学年学费是多少？", "4800", "finance.ustc.edu.cn"),
    ("住宿费多少？", "1000", "finance.ustc.edu.cn"),
    ("学生优惠火车票怎么核验资质？", "12306", "12306"),
    ("新生报到流程？", "报到", "news.ustc.edu.cn"),
    ("国家助学贷款本科一年能贷多少？", "8000", "xxgk.ustc.edu.cn"),
    ("勤工助学每小时多少钱？", "20", "xxgk.ustc.edu.cn"),
    ("毕业离校要办哪些手续？", "离校", "stuhome.ustc.edu.cn"),
    ("有没有困难学生资助？", "绿色通道", "zsb.ustc.edu.cn"),
]

ok_c = fail_c = 0
for q, want, dom in CASES:
    t0 = time.time()
    try:
        r = run_qa(q, module_signal="智能问答", student_id="PB25111691",
                   user_profile={"name":"测试","major":"人工智能","grade":"2025级","logged_in":True})
        ans = r.get("answer") or r.get("clarify_question") or ""
        err = r.get("error") or ""
    except Exception as e:
        ans = f"⚠ {type(e).__name__}: {e}"; err = str(e)
    has_want = want in ans
    has_url = dom in ans
    ok = has_want and has_url
    tag = "PASS" if ok else "FAIL"
    if ok: ok_c += 1
    else: fail_c += 1
    print(f"[{tag}] {q}  want='{want}'({has_want}) url='{dom}'({has_url}) err={err!r}")
    print(f"   ans: {ans[:180]}".replace("\n"," ⏎ "))
print(f"\n结果: {ok_c}/{ok_c+fail_c} 通过")

# 保存完整原文
out = Path(__file__).resolve().parents[1] / "docs" / "知识库补录_综合QA_回答原文.md"
lines = []
bye = False
for q, want, dom in CASES:
    pass
print("已完成。")
