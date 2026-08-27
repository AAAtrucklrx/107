import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { AcademicWorkspace } from "./AcademicWorkspace";
import type { SessionPayload } from "../types";

vi.mock("../lib/api", () => ({
  apiGet: vi.fn((path: string) => {
    const source = { kind: "demo_fixture", label: "合成演示数据", demo: true, stale: false };
    if (path.endsWith("/overview")) return Promise.resolve({
      identity: { id: "PB25111691", name: "测试", major: "人工智能", grade: "2025级" },
      metrics: { gpa: 3.5, completed_credits: 25, current_credits: 12.5, grade_count: 6 },
      recent_grades: [], source, limitations: [],
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
      progress: null,
      source,
      banner: "演示数据：合成个人培养方案",
      limitations: [],
    });
    if (path.endsWith("/courses")) return Promise.resolve({ courses: [], grades: [], source, limitations: [] });
    return Promise.resolve({ semester: "2026年春季学期", courses: [], source, limitations: [] });
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
  const actions = screen.getAllByRole("button", { name: "问问小蜗" });
  expect(actions).toHaveLength(1);
  expect(screen.queryByText("查冲突")).not.toBeInTheDocument();
  expect(screen.queryByText("加日程")).not.toBeInTheDocument();
  await user.click(actions[0]);
  expect(onAsk).toHaveBeenCalledWith("点评培养方案中的课程“人工智能导论”");
});
