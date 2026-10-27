// Synthetic UI fixtures only. No real student, school cookie, token, or backend.
export const fixtureDate = "2026-10-26T10:00:00+08:00";
const source = { kind: "ui_fixture", label: "合成验收数据", demo: true, stale: false };
export const session = {
  jw_session: true,
  jw_identity: { student_id: "TEST-2026", name: "合成同学", college: "演示学院", major: "计算机科学与技术", grade: "2025级" },
  principal: {
    id: "TEST-2026", auth_mode: "cas", authenticated: true,
    profile: { id: "TEST-2026", name: "合成同学", major: "计算机科学与技术", grade: "2025级", profile_source: "ui_fixture" },
    is_admin: false, review_namespace: null,
  },
  capabilities: { public_chat: true, server_history: true, personal_academic: true, knowledge_review: false, production_publish: false },
  csrf_token: "synthetic-ui-fixture",
};

const courses = [
  ["SYN101", "数据结构与算法", "演示教师甲", 4, 1, "07:50", "09:25", "合成教学楼 A101"],
  ["SYN102", "离散数学", "演示教师乙", 3, 1, "09:45", "11:20", "合成教学楼 A202"],
  ["SYN103", "人工智能导论与跨学科应用实践", "演示教师丙", 3, 2, "14:00", "15:35", "合成教学楼 B303"],
  ["SYN104", "大学物理", "演示教师丁", 4, 3, "07:50", "09:25", "合成教学楼 C404"],
  ["SYN105", "线性代数", "演示教师戊", 3, 4, "09:45", "11:20", "合成教学楼 D505"],
  ["SYN106", "程序设计实验", "演示教师己", 2, 5, "14:00", "15:35", "合成实验室 E606"],
].map(([course_code, course_name, teacher, credits, weekday, start_time, end_time, location]) => ({
  course_code, course_name, teacher, credits, semester: "2026-2027-1", location,
  meetings: [{ meeting_id: `${course_code}-meeting`, weekday, day: ["", "周一", "周二", "周三", "周四", "周五"][weekday], week_numbers: [8, 9, 10], weeks: "第8–10周", periods: [1, 2], period_label: "第1–2节", start_time, end_time, location, raw: "仅用于界面验收的合成排课" }],
}));

export const schedule = {
  semester: "2026-2027-1", semester_code: "2026-2027-1", semester_start: "2026-08-31",
  total_weeks: 18, current_week: 9, courses,
  unparsed_courses: [{ course_code: "SYN-UNKNOWN", course_name: "合成研究训练", raw_schedule: "时间待确认", reason: "数据源未提供可确认的排课时间。" }],
  source, limitations: ["全部课程、身份与成绩均为合成验收数据，不用于真实教学判断。"],
};

const grades = [
  { course_name: "数学分析", semester: "2025年秋季学期", credits: 5, score: 91, grade_point: 3.7 },
  { course_name: "程序设计基础", semester: "2025年秋季学期", credits: 4, score: 95, grade_point: 4 },
  { course_name: "线性代数", semester: "2026年春季学期", credits: 4, score: 88, grade_point: 3.3 },
  { course_name: "大学英语与跨文化交流（合成超长课程名）", semester: "2026年春季学期", credits: 2, score: "优秀", grade_point: 4 },
  { course_name: "尚未出分的合成课程", semester: "2026年秋季学期", credits: 2 },
];

const program = {
  program: { name: "计算机科学与技术培养方案（合成）", college: "演示学院", grade: "2025级", totalCredits: 160,
    modules: [{ category: "通识教育", required_credits: 30, course_count: 10 }, { category: "专业核心", required_credits: 60, course_count: 18 }],
    courses: [
      { code: "SYN-P101", name: "大学英语", required: "必修", credit: 2, term: "1秋", category: "通识教育/语言" },
      { code: "SYN-P102", name: "线性代数", required: "必修", credit: 4, term: "1春", category: "专业核心/基础" },
      { code: "SYN-P103", name: "算法设计与分析", required: "必修", credit: 4, term: "2秋", category: "专业核心/计算机" },
    ] },
  progress: { taken_courses_known: true, percent: 31.25, credits_taken: 25, credits_required: 80,
    modules_progress: [{ category: "通识教育", taken: 3, total: 10 }, { category: "专业核心", taken: 5, total: 18 }] },
  banner: "合成验收数据：仅用于界面走查", source, limitations: ["此数据不代表任何真实学生的培养进度。"],
};

export const wideTable = {
  title: "合成长表格：课程比较", source_tool: "ui_fixture",
  columns: ["课程", "学分", "教师", "时间", "校区", "考核方式", "课程简介", "来源"],
  rows: courses.map((course) => [course.course_name, String(course.credits), course.teacher, "周一 07:50–09:25", "合成校区", "课程作业与期末考试", "用于验收长表格在小屏幕内部滚动，文字不应撑开整个页面。", "合成验收数据"]),
};

const answer = "以下是合成验收数据，用于检查长表格与多行回答。\n\n所有课程与身份信息均为虚构，未向真实后台发送请求。";
export const events = [
  { type: "stage.changed", data: { stage: "action", message: "正在整理合成课程记录…" } },
  { type: "data.table", data: wideTable },
  { type: "answer.segment", data: { markdown: answer } },
  { type: "answer.completed", data: { answer_id: "synthetic-answer", claims: [], sources: [], limitations: ["合成数据，仅用于前端验收。"], terminal_reason: "completed" } },
].map((event, index) => `data: ${JSON.stringify({ id: index + 1, run_id: "synthetic-run", at: fixtureDate, ...event })}\n\n`).join("");

export function apiFixture(path, method) {
  if (path === "/config/public") return {
    environment: "development", auth_mode: "cas", version: "synthetic-ui-verification",
    features: { chat: true, web_search: true, personal_workspace: true, review_workspace: false, ingestion_worker: false, demo_reset_enabled: false },
    time_budget_seconds: { search: 4, evidence: 12, generation: 18, total: 20 },
  };
  if (path === "/auth/session") return session;
  if (path === "/academic/overview") return { identity: session.principal.profile, metrics: { gpa: 3.62, completed_credits: 25, current_credits: 19, grade_count: 5 }, grades, recent_grades: grades, source, limitations: [] };
  if (path === "/academic/courses") return { courses, grades, source, limitations: [] };
  if (path === "/academic/schedule") return schedule;
  if (path === "/academic/program") return program;
  if (path === "/campus/activities") return { items: [], source, limitations: [] };
  if (path === "/conversations") return { items: [] };
  if (path === "/chat/runs" && method === "POST") return { run_id: "synthetic-run", conversation_id: null, requested_mode: "auto", effective_mode: "auto", events_url: "/chat/runs/synthetic-run/events" };
  return undefined;
}
