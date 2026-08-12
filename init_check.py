"""
小蜗 — 初始化验证脚本
运行数据库初始化 + 知识库构建
"""

import sys
from pathlib import Path

# 使用相对路径（兼容不同运行环境）
sys.path.insert(0, str(Path(__file__).parent))

from config import DATABASE_PATH, SCHEMA_PATH, KNOWLEDGE_DATA_DIR, CHROMA_PERSIST_DIR
from services.service_container import ServiceContainer
from utils.logger import get_logger

log = get_logger("xiaowo.init")

print("=" * 60)
print("🐌 小蜗 - 项目初始化验证")
print("=" * 60)

# 1. 初始化服务容器
print("\n[1/4] 初始化服务容器...")
container = ServiceContainer()

# 2. 初始化数据库
print("\n[2/4] 初始化数据库...")
db = container.init_database(DATABASE_PATH, SCHEMA_PATH, seed_sql=None)
db.init_schema(SCHEMA_PATH)
print(f"  数据库路径: {DATABASE_PATH}")

# 3. 导入种子数据
print("\n[3/4] 导入种子数据...")
from database.seed_data import SEED_SQL

existing = db.query_one("SELECT COUNT(*) as cnt FROM student_courses")
if existing and existing.get("cnt", 0) > 0:
    print(f"  种子数据已存在: {existing['cnt']} 门课程")
else:
    db.run_script(SEED_SQL)
    course_count = db.query_one("SELECT COUNT(*) as cnt FROM student_courses")
    grade_count = db.query_one("SELECT COUNT(*) as cnt FROM student_grades")
    print(f"  课程: {course_count['cnt']} 门, 成绩: {grade_count['cnt']} 条")

# 3.5 评课数据库（course_data.db, 由 scripts/build_course_db.py 构建）
from pathlib import Path as _P
course_db = _P(__file__).parent / "data" / "course_data.db"
if course_db.exists():
    import sqlite3
    _c = sqlite3.connect(str(course_db))
    _rc = _c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    _cc = _c.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    _c.close()
    print(f"  评课库: {_cc} 门课程, {_rc} 条真实评论")
else:
    print("  评课库: 未找到 data/course_data.db（运行 python scripts/crawl_icourse.py all && python scripts/build_course_db.py 构建）")

# 4. 知识库加载
print("\n[4/4] 加载知识库文档并构建向量索引...")
from knowledge.document_loader import load_faq_documents

docs = load_faq_documents(KNOWLEDGE_DATA_DIR)
print(f"  找到 {len(docs)} 篇 FAQ 文档")
for cat in ["办事", "就业", "教务", "生活", "科研与升学"]:
    count = sum(1 for d in docs if d["metadata"].get("category") == cat)
    print(f"    {cat}: {count} 篇")

store = container.init_vector_store(CHROMA_PERSIST_DIR)
if store.count() == 0 and docs:
    store.add_documents(docs)
print(f"  知识库: {store.count()} 条向量")

# 测试检索
print("\n" + "=" * 60)
print("🔍 测试检索: '学生证丢了怎么办'")
result = store.search("学生证丢了怎么办")
print(f"  Top score: {result['top_score']}")
print(f"  Found: {result['found']}")
if result["results"]:
    print(f"  #1: [{result['results'][0]['category']}] score={result['results'][0]['score']}")
    print(f"  Preview: {result['results'][0]['content'][:80]}...")

print("\n" + "=" * 60)
print("✅ 初始化完成！运行: streamlit run app.py")
print("=" * 60)
