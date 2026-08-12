"""
小蜗 — 种子数据模块
课程/评课基于真实 catalog API 抓取 (2026 春季学期)；
登录后由 jw API 实时拉取个人数据
"""

SEED_SQL = """
-- ============================================
-- 小蜗 种子数据 (课程/评课基于真实 catalog API 抓取;
--              个人数据登录后由 jw API 实时拉取)
-- ============================================

-- 课程信息
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (1, 'MATH1007', '数学分析(B2)', '郑业龙', 6, '1~5,7~9,11~18周 5301 :1(3,4) 郑业龙 1~18周 5301 :3(3,4) 郑业龙 1~8,1', '5301', '2025-2026-2');
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (2, 'MATH1009', '线性代数(B1)', '马立明', 4, '1~5,7~9,11~18周 3C101 :1(6,7) 马立明 1~18周 3C101 :4(3,4) 马立明 10周', '', '2025-2026-2');
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (3, 'CS2502A', '数据结构A', '', 4, '1~9,11~16周 3A313 :2(8,9) 张辉 1~9,11~16周 西区电三楼上机 :2(19:00~19:3', '', '2025-2026-2');
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (4, 'CS1514', '面向交叉学科的Python程序设计与跨学科实践', '邢凯', 3, '1~14周 3A109 :3(19:00~19:30) 邢凯 1~14周 3A109 :3(11,12) 邢凯', '', '2025-2026-2');
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (5, '022164', '大学物理-现代技术实验', '岳盈', 1.5, '3~16周 东区教1楼物理实验室 :4(6,7,8,9) 蔡俊 3~16周 东区教1楼物理实验室 :4(6,7,8,9)', '', '2025-2026-2');
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (6, 'FL1002', '基础英语II', '徐戎荣', 3, '1~5,7~9,11~18周 2104 :1(1,2) 徐戎荣 1~18周 2104 :3(1,2) 徐戎荣 10周 2', '2104', '2025-2026-2');
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (7, '011705', '操作系统原理与设计(H)', '邢凯', 4, '1~5,7~9,11~15周 3A110 :1(8,9) 邢凯 1~15周 3A110 :3(1,2) 邢凯 10周 3', '', '2025-2026-2');
INSERT OR REPLACE INTO courses (id, code, name, teacher, credits, time, location, semester) VALUES (8, 'MATH3013', '概率论进阶', '刘党政', 1, '13~16周 5401 :1(3,4,5) 刘党政 13~16周 5401 :4(6,7) 刘党政', '5401', '2025-2026-2');

-- 评课社区数据
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (1, 'CS2001', '机器学习导论', '王教授', 8.7, 6.5, 5.0, '给分好', '人工智能,机器学习', 45, '内容充实，讲课清晰');
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (2, 'CS2002', '算法设计与分析', '李教授', 8.5, 7.2, 6.0, '给分一般', '算法,数据结构', 38, '核心课，难度较高但收获大');
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (3, 'CS2003', '操作系统', '周教授', 7.8, 7.5, 7.0, '给分一般', '系统,C语言', 52, '硬核课程，实验量大');
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (4, 'CS2004', '数据库系统', '赵教授', 8.2, 5.5, 4.5, '给分好', '数据库,SQL', 30, '实用性强，讲课清楚');
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (5, 'MATH2001', '概率论与数理统计', '孙教授', 8.9, 6.0, 5.5, '给分好', '数学,统计', 60, '基础课，讲得很好');
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (6, 'ENG2001', '学术英语写作', '陈教授', 7.5, 4.5, 5.0, '给分好', '英语,写作', 22, '对写论文有帮助');
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (7, 'PHYS2001', '大学物理B', '张教授', 8.0, 7.8, 7.5, '给分一般', '物理,实验', 35, '难度不低但讲得清楚');
INSERT OR REPLACE INTO course_reviews (id, course_code, course_name, teacher, rating, difficulty, workload, give_score, tags, review_count, review_summary) VALUES (8, 'BIO2001', '生命科学导论', '杨教授', 9.0, 3.5, 3.0, '给分好', '生物,通识', 28, '非常有趣的通识课');

-- 教师评价
INSERT OR REPLACE INTO teacher_reviews (id, name, courses, avg_rating, teaching_style, strengths, weaknesses, review_summary, review_count) VALUES (1, '李教授', '数学分析B1,数学分析B2', 8.5, '讲课清晰、板书详细', '讲解透彻', '进度快', '讲课质量高，数学基础扎实', 120);
INSERT OR REPLACE INTO teacher_reviews (id, name, courses, avg_rating, teaching_style, strengths, weaknesses, review_summary, review_count) VALUES (2, '王教授', '机器学习导论,深度学习', 8.8, '善于结合实例', '科研能力强', '实验要求高', 'AI课程紧跟前沿', 85);
INSERT OR REPLACE INTO teacher_reviews (id, name, courses, avg_rating, teaching_style, strengths, weaknesses, review_summary, review_count) VALUES (3, '张教授', '大学物理B,力学', 8.0, '讲课细致', '态度认真', '课程偏难', '物理课难度不低但讲得清楚', 70);
"""
