"""
小蜗 — 种子数据模块
基于真实 catalog API 数据生成 (2026 春季学期)
"""

SEED_SQL = """
-- ============================================
-- 小蜗 种子数据 (基于真实 catalog API 数据)
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

"""
