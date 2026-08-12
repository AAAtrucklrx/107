-- 选课推荐数据库 schema（data/course_data.db）
-- 数据来源: icourse.club（课程/评论）+ 培养方案（programs）
-- 主评分 = 评论星级真实均分（不归一化）; 维度均分为文字映射分（1-10）仅作参考展示

PRAGMA journal_mode = WAL;

-- 课程（按 课名+开课单位 合并 icourse 多门同课）
CREATE TABLE IF NOT EXISTS courses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,               -- 课程名（不含老师）
    dept          TEXT NOT NULL DEFAULT '',    -- 开课单位
    code          TEXT NOT NULL DEFAULT '',    -- 课程号（取首个非空）
    credit        REAL,                        -- 学分（取首个非空）
    course_type   TEXT NOT NULL DEFAULT '',    -- 本科/研究生等
    course_level  TEXT NOT NULL DEFAULT '',    -- 通修/专业核心等
    icourse_ids   TEXT NOT NULL DEFAULT '[]',  -- 合并的 icourse 课程 id 列表 JSON
    rating_avg    REAL NOT NULL DEFAULT 0,     -- 星级均分（0-10）
    rate_count    INTEGER NOT NULL DEFAULT 0,  -- 评论人数
    UNIQUE (name, dept)
);
CREATE INDEX IF NOT EXISTS idx_courses_name  ON courses(name);
CREATE INDEX IF NOT EXISTS idx_courses_code  ON courses(code);
CREATE INDEX IF NOT EXISTS idx_courses_rating ON courses(rating_avg);

-- 评论（原始数据, 老师维度从 teacher 列聚合）
CREATE TABLE IF NOT EXISTS reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES courses(id),
    icourse_id   INTEGER NOT NULL,             -- 来源 icourse 课程 id
    review_iid   INTEGER NOT NULL,             -- icourse 评论 id
    teacher      TEXT NOT NULL DEFAULT '',     -- 该 icourse 课程标题中的老师
    author       TEXT NOT NULL DEFAULT '',
    stars        REAL NOT NULL DEFAULT 0,   -- 1-5 星（支持半星 0.5 步进, 0=未评分评论）
    term         TEXT NOT NULL DEFAULT '',     -- 开课学期文本（如 2023秋）
    difficulty   TEXT NOT NULL DEFAULT '',     -- 课程难度: 简单/中等/困难
    homework     TEXT NOT NULL DEFAULT '',     -- 作业多少: 很少/少/中等/多/很多
    give_score   TEXT NOT NULL DEFAULT '',     -- 给分好坏: 很差/差/一般/好/超好
    harvest      TEXT NOT NULL DEFAULT '',     -- 收获大小: 没有/少/一般/多/很多
    content      TEXT NOT NULL DEFAULT '',     -- 评论原文
    UNIQUE (icourse_id, review_iid)  -- 同课多师合并后 course_id 下不同 icourse 页评论 id 可能重复, 以来源页为准
);
CREATE INDEX IF NOT EXISTS idx_reviews_course ON reviews(course_id);
CREATE INDEX IF NOT EXISTS idx_reviews_teacher ON reviews(teacher);

-- 预聚合: 课程维度（星级均分 + 四维度映射均分 + 文本分布）
CREATE TABLE IF NOT EXISTS course_rates (
    course_id        INTEGER PRIMARY KEY REFERENCES courses(id),
    rating_sum       REAL NOT NULL DEFAULT 0,
    rating_count     INTEGER NOT NULL DEFAULT 0,
    rating_avg       REAL NOT NULL DEFAULT 0,
    diff_sum         REAL NOT NULL DEFAULT 0,  -- 难度映射分
    diff_count       INTEGER NOT NULL DEFAULT 0,
    diff_avg         REAL NOT NULL DEFAULT 0,
    hw_sum           REAL NOT NULL DEFAULT 0,  -- 作业映射分
    hw_count         INTEGER NOT NULL DEFAULT 0,
    hw_avg           REAL NOT NULL DEFAULT 0,
    score_sum        REAL NOT NULL DEFAULT 0,  -- 给分映射分
    score_count      INTEGER NOT NULL DEFAULT 0,
    score_avg        REAL NOT NULL DEFAULT 0,
    gain_sum         REAL NOT NULL DEFAULT 0,  -- 收获映射分
    gain_count       INTEGER NOT NULL DEFAULT 0,
    gain_avg         REAL NOT NULL DEFAULT 0,
    dims_dist        TEXT NOT NULL DEFAULT '{}' -- 维度文本分布 JSON
);
CREATE INDEX IF NOT EXISTS idx_rates_rating ON course_rates(rating_avg);

-- 开课学期（YYYYN 编码: 1=秋 2=春 3=夏）
CREATE TABLE IF NOT EXISTS course_terms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id),
    term       INTEGER NOT NULL,
    UNIQUE (course_id, term)
);
CREATE INDEX IF NOT EXISTS idx_terms_course ON course_terms(course_id);

-- 教师
CREATE TABLE IF NOT EXISTS teachers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE
);

-- 教师粒度聚合（同课多师: 各自星级均分/样本量/分布）
CREATE TABLE IF NOT EXISTS course_teachers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES courses(id),
    teacher_id   INTEGER NOT NULL REFERENCES teachers(id),
    rating_sum   REAL NOT NULL DEFAULT 0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    rating_avg   REAL NOT NULL DEFAULT 0,
    dims_dist    TEXT NOT NULL DEFAULT '{}',
    UNIQUE (course_id, teacher_id)
);
CREATE INDEX IF NOT EXISTS idx_ct_course ON course_teachers(course_id);

-- 培养方案（icourse /program/{pid}/）
CREATE TABLE IF NOT EXISTS programs (
    id        INTEGER PRIMARY KEY,   -- icourse pid
    name      TEXT NOT NULL DEFAULT '',
    college   TEXT NOT NULL DEFAULT '',
    grade     TEXT NOT NULL DEFAULT ''   -- 如 2025级
);

-- 方案课程行（course_id 构建时按 code/name 尽力匹配, 可为 NULL）
CREATE TABLE IF NOT EXISTS program_courses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id   INTEGER NOT NULL REFERENCES programs(id),
    course_id    INTEGER REFERENCES courses(id),
    code         TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL DEFAULT '',
    required     TEXT NOT NULL DEFAULT '',  -- 必修/选修
    exam         TEXT NOT NULL DEFAULT '',  -- 考核形式
    credit       TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    term         TEXT NOT NULL DEFAULT ''   -- 学期标注 如 1秋 / 2秋,3春
);
CREATE INDEX IF NOT EXISTS idx_pc_program ON program_courses(program_id);
CREATE INDEX IF NOT EXISTS idx_pc_course ON program_courses(course_id);
CREATE INDEX IF NOT EXISTS idx_pc_code ON program_courses(code);
