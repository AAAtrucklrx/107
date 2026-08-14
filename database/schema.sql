-- ============================================
-- 小蜗 数据库 Schema
-- SQLite
-- ============================================

-- 课程信息缓存表
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    name TEXT NOT NULL,
    teacher TEXT,
    credits REAL,
    time TEXT,
    location TEXT,
    semester TEXT,
    capacity INTEGER,
    description TEXT
);

-- 学生课表关联表
CREATE TABLE IF NOT EXISTS student_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    course_code TEXT NOT NULL,
    course_name TEXT NOT NULL,
    teacher TEXT,
    credits REAL,
    time TEXT,
    location TEXT,
    semester TEXT DEFAULT '2025-2026-2'
);

-- 学生成绩表
CREATE TABLE IF NOT EXISTS student_grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    semester TEXT NOT NULL,
    course_name TEXT NOT NULL,
    credits REAL NOT NULL,
    score INTEGER NOT NULL,          -- 百分制分数；等级制（优秀/通过…）存哨兵 -1
    score_text TEXT,                 -- 等级制成绩原文（百分制为 NULL）
    grade_point REAL NOT NULL
);

-- 评课社区数据缓存表
CREATE TABLE IF NOT EXISTS course_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_code TEXT,
    course_name TEXT NOT NULL,
    teacher TEXT,
    rating REAL,
    difficulty REAL,
    workload REAL,
    give_score TEXT,
    tags TEXT,
    review_count INTEGER DEFAULT 0,
    review_summary TEXT,
    last_updated TEXT
);

-- 教师评价缓存表
CREATE TABLE IF NOT EXISTS teacher_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    courses TEXT,
    avg_rating REAL,
    teaching_style TEXT,
    strengths TEXT,
    weaknesses TEXT,
    review_summary TEXT,
    review_count INTEGER DEFAULT 0
);

-- 日程事件表
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    location TEXT,
    description TEXT,
    is_recurring INTEGER DEFAULT 0,
    source TEXT DEFAULT 'manual',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 提醒表
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    remind_at TEXT NOT NULL,
    is_triggered INTEGER DEFAULT 0,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

-- 对话历史表（可选持久化）
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    module TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
);