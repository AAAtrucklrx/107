import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { AcademicWorkspace } from "./AcademicWorkspace";
import type { GradeRecord, SessionPayload } from "../types";

const academicFixtures = vi.hoisted(() => ({
  progress: null as Record<string, unknown> | null,
  grades: [] as GradeRecord[],
  gpa: 3.5 as number | null,
}));

beforeEach(() => {
  academicFixtures.progress = null;
  academicFixtures.grades = [];
  academicFixtures.gpa = 3.5;
});

vi.mock("@fullcalendar/timegrid", () => ({ default: {} }));
vi.mock("@fullcalendar/react", () => ({
  default: ({
    events,
    eventContent,
    eventClick,
  }: {
    events: Array<{ id: string; extendedProps: Record<string, unknown> }>;
    eventContent: (info: { event: { extendedProps: Record<string, unknown> } }) => ReactNode;
    eventClick: (info: { event: { extendedProps: Record<string, unknown> } }) => void;
  }) => (
    <div data-testid="schedule-calendar">
      {events.map((event) => (
        <button
          type="button"
          key={event.id}
          onClick={() => eventClick({ event: { extendedProps: event.extendedProps } })}
        >
          {eventContent({ event: { extendedProps: event.extendedProps } })}
        </button>
      ))}
    </div>
  ),
}));

vi.mock("../lib/api", () => ({
  apiGet: vi.fn((path: string) => {
    const source = { kind: "demo_fixture", label: "合成演示数据", demo: true, stale: false };
    if (path.endsWith("/overview")) return Promise.resolve({
      identity: { id: "PB25111691", name: "测试", major: "人工智能", grade: "2025级" },
      metrics: { gpa: academicFixtures.gpa, completed_credits: 25, current_credits: 12.5, grade_count: 6 },
      recent_grades: academicFixtures.grades, source, limitations: [],
    });
    if (path.endsWith("/program")) return Promise.resolve({
      program: {
        name: "人工智能专业培养方案（演示）",
        college: "人工智能与数据科学学院",
        grade: "2025级",
        totalCredits: 160,
        modules: [],
        courses: [{ code: "AI2001", name: "人工智能导论", required: "必修", credit: 3, term: "2秋", category: "专业核心" }],
      },
      progress: academicFixtures.progress,
      source,
      banner: "演示数据：合成个人培养方案",
      limitations: [],
    });
    if (path.endsWith("/courses")) return Promise.resolve({ courses: [], grades: [], source, limitations: [] });
    return Promise.resolve({
      semester: "2026-2027-1",
      semester_code: "2026-2027-1",
      semester_start: "2026-08-31",
      total_weeks: 18,
      current_week: 1,
      courses: [
        {
          course_code: "CS2502A",
          course_name: "数据结构A",
          teacher: "演示教师乙",
          credits: 4,
          semester: "2026-2027-1",
          meetings: [{
            meeting_id: "CS2502A-0-1-07:50",
            weekday: 1,
            day: "周一",
            week_numbers: [1],
            weeks: "1周",
            periods: [1, 2],
            period_label: "第1-2节",
            start_time: "07:50",
            end_time: "09:25",
            location: "3A313",
            raw: "周一 1周 第1-2节",
          }],
        },
        {
          course_code: "CS2003",
          course_name: "系统程序设计基础",
          teacher: "演示教师己",
          credits: 2.5,
          semester: "2026-2027-1",
          meetings: [{
            meeting_id: "CS2003-0-1-08:40",
            weekday: 1,
            day: "周一",
            week_numbers: [1],
            weeks: "1周",
            periods: [2, 3],
            period_label: "第2-3节",
            start_time: "08:40",
            end_time: "10:30",
            location: "3C302",
            raw: "周一 1周 第2-3节",
          }],
        },
      ],
      unparsed_courses: [{
        course_code: "UNPARSED",
        course_name: "待确认课程",
        reason: "排课缺少可确认的星期、周次或起止时间。",
        raw_schedule: "时间待定",
      }],
      source,
      limitations: ["演示课表为合成数据，不用于真实到课判断。"],
    });
  }),
}));

const session: SessionPayload = {
  principal: {
    id: "PB25111691", auth_mode: "demo", authenticated: true,
    profile: { id: "PB25111691", name: "测试", major: "人工智能", grade: "2025级" },
    is_admin: false, review_namespace: null,
  },
  capabilities: { public_chat: true, server_history: true, personal_academic: true, knowledge_review: false, production_publish: false },
  csrf_token: "csrf",
};

test("program keeps identity/source labels and one Xiaowo action per course", async () => {
  const user = userEvent.setup();
  const onAsk = vi.fn();
  render(<AcademicWorkspace session={session} onAsk={onAsk} />);
  await waitFor(() => expect(screen.getByText("我的学业")).toBeInTheDocument());
  expect(screen.getByText("人工智能 · 2025级")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: /培养方案/ }));
  expect(screen.getByText("演示数据：合成个人培养方案")).toBeInTheDocument();
  expect(screen.getByText("人工智能专业培养方案（演示）")).toBeInTheDocument();
  const actions = screen.getAllByRole("button", { name: "问问小蛙助手" });
  expect(actions).toHaveLength(1);
  expect(screen.queryByText("查冲突")).not.toBeInTheDocument();
  expect(screen.queryByText("加日程")).not.toBeInTheDocument();
  await user.click(actions[0]);
  expect(onAsk).toHaveBeenCalledWith("点评培养方案中的课程“人工智能导论”");
});

test("schedule filters by week, marks overlaps, and opens one course action", async () => {
  const user = userEvent.setup();
  const onAsk = vi.fn();
  render(<AcademicWorkspace session={session} onAsk={onAsk} />);
  await waitFor(() => expect(screen.getByText("我的学业")).toBeInTheDocument());
  await user.click(screen.getByRole("tab", { name: /课表/ }));

  expect(screen.getByRole("heading", { name: "第 1 周" })).toBeInTheDocument();
  expect(screen.getByText("08月31日 - 09月06日")).toBeInTheDocument();
  expect(screen.getByLabelText("课时划分")).toHaveTextContent("第一节07:50-09:25");
  expect(screen.getByLabelText("课时划分")).toHaveTextContent("第四节15:55-18:20");
  expect(screen.getAllByLabelText("与同时间课程重叠")).toHaveLength(2);
  expect(screen.getByText("待确认的排课")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /数据结构A/ }));
  expect(screen.getByRole("dialog")).toHaveTextContent("周一 07:50-09:25");
  expect(screen.getByRole("dialog")).toHaveTextContent("3A313");
  expect(screen.getByRole("dialog")).toHaveTextContent("演示教师乙");
  const ask = screen.getByRole("button", { name: "问问小蛙助手" });
  await user.click(ask);
  expect(onAsk).toHaveBeenCalledWith("点评课表中的课程“数据结构A”");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  await user.click(screen.getByRole("button", { name: "后一周" }));
  expect(screen.getByRole("heading", { name: "第 2 周" })).toBeInTheDocument();
  expect(screen.getByText("第 2 周暂无可确认课程")).toBeInTheDocument();
});

test.each([
  null,
  { taken_courses_known: false, percent: 0, credits_taken: 0, credits_required: 160, modules_progress: [{ category: "专业核心", taken: 0, total: 8 }] },
  { taken_courses_known: true, percent: null, credits_taken: 0, credits_required: 160 },
])("unconfirmed program progress never presents placeholder zero as completion (%j)", async (progress) => {
  academicFixtures.progress = progress;
  const user = userEvent.setup();
  render(<AcademicWorkspace session={session} onAsk={vi.fn()} />);
  await screen.findByText("我的学业");
  await user.click(screen.getByRole("tab", { name: /培养方案/ }));

  expect(screen.getByText("完成情况暂不可确认")).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.queryByText("0%")).not.toBeInTheDocument();
});

test("program progress uses confirmed service values and keeps module completion in courses", async () => {
  academicFixtures.progress = {
    taken_courses_known: true,
    percent: 18.75,
    credits_taken: 3,
    credits_required: 16,
    modules_progress: [{ category: "专业核心", taken: 1, total: 4 }],
  };
  const user = userEvent.setup();
  const onAsk = vi.fn();
  render(<AcademicWorkspace session={session} onAsk={onAsk} />);
  await screen.findByText("我的学业");
  await user.click(screen.getByRole("tab", { name: /培养方案/ }));

  const totalProgress = screen.getByRole("region", { name: "必修学分进度" });
  expect(within(totalProgress).getByText("18.75%")).toBeInTheDocument();
  expect(within(totalProgress).getByText("3 / 16 学分")).toBeInTheDocument();
  expect(within(totalProgress).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "18.75");
  const module = screen.getByRole("region", { name: "专业核心模块" });
  expect(within(module).getByText("已匹配 1 / 4 门必修课程")).toBeInTheDocument();
  expect(within(module).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  expect(within(module).getByText("人工智能导论")).toBeInTheDocument();
  await user.click(within(module).getByRole("button", { name: "问问小蛙助手" }));
  expect(onAsk).toHaveBeenCalledWith("点评培养方案中的课程“人工智能导论”");
});

test("a confirmed zero is displayed as zero rather than missing progress", async () => {
  academicFixtures.progress = {
    taken_courses_known: true, percent: 0, credits_taken: 0, credits_required: 16,
    modules_progress: [{ category: "专业核心", taken: 0, total: 4 }],
  };
  const user = userEvent.setup();
  render(<AcademicWorkspace session={session} onAsk={vi.fn()} />);
  await screen.findByText("我的学业");
  await user.click(screen.getByRole("tab", { name: /培养方案/ }));
  expect(screen.getByText("0%")).toBeInTheDocument();
  expect(screen.getByText("0 / 16 学分")).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "专业核心必修课程匹配进度" })).toHaveAttribute("aria-valuenow", "0");
});

test("grade cards preserve zero scores and missing values without deriving GPA", async () => {
  academicFixtures.gpa = null;
  academicFixtures.grades = [
    { course_name: "未出分课程", semester: "2026年秋季学期", credits: 2 },
    { course_name: "零分记录", semester: "2026年秋季学期", credits: 0, score: 0, grade_point: 0 },
  ];
  render(<AcademicWorkspace session={session} onAsk={vi.fn()} />);
  await screen.findByText("我的学业");
  expect(within(screen.getByRole("region", { name: "累计绩点" })).getByText("--")).toBeInTheDocument();
  const table = screen.getByRole("table", { name: "2026年秋季学期成绩" });
  expect(within(table).getAllByRole("row")).toHaveLength(3);
  expect(within(within(table).getByRole("row", { name: /零分记录/ })).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["零分记录", "0", "0", "0"]);
  expect(within(within(table).getByRole("row", { name: /未出分课程/ })).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["未出分课程", "2", "--", "--"]);
});
