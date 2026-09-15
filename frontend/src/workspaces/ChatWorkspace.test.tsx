import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import * as Tooltip from "@radix-ui/react-tooltip";
import { ChatWorkspace } from "./ChatWorkspace";
import { streamRunEvents } from "../lib/api";
import type { PublicConfig, SessionPayload, SseEnvelope } from "../types";

const putLocalConversation = vi.fn((_value: unknown) => Promise.resolve());

vi.mock("../lib/anonymousHistory", () => ({
  listLocalConversations: vi.fn(() => Promise.resolve([])),
  putLocalConversation: (value: unknown) => putLocalConversation(value),
  deleteLocalConversation: vi.fn(() => Promise.resolve()),
  clearLocalConversations: vi.fn(() => Promise.resolve()),
}));

vi.mock("../lib/api", () => ({
  apiDelete: vi.fn(),
  apiGet: vi.fn(),
  apiMutation: vi.fn(() => Promise.resolve({
    run_id: "run-fixture",
    conversation_id: null,
    requested_mode: "auto",
    effective_mode: "auto",
    events_url: "/api/v1/chat/runs/run-fixture/events",
  })),
  streamRunEvents: vi.fn(async (_path: string, options: { onEvent: (event: SseEnvelope) => void }) => {
    options.onEvent({ id: 1, run_id: "run-fixture", type: "stage.changed", at: "2026-08-27T00:00:00Z", data: { stage: "evidence_check" } });
    options.onEvent({ id: 2, run_id: "run-fixture", type: "answer.segment", at: "2026-08-27T00:00:01Z", data: { segment_id: "seg", markdown: "已核验的完整回答。[1]", claim_ids: ["c1"] } });
    options.onEvent({
      id: 3,
      run_id: "run-fixture",
      type: "answer.completed",
      at: "2026-08-27T00:00:02Z",
      data: {
        answer_id: "answer-fixture",
        claims: [],
        sources: [{
          source_id: "s1", title: "教务处来源", display_url: "https://www.teach.ustc.edu.cn/",
          institution: "中国科学技术大学教务处", domain: "www.teach.ustc.edu.cn",
          published_at: null, fetched_at: "2026-08-27T00:00:00Z", level: "official_primary",
          validity: "valid", citation: 1,
        }],
        limitations: [], terminal_reason: "web_evidence_confirmed",
      },
    });
  }),
}));

const config: PublicConfig = {
  environment: "development", auth_mode: "anonymous", version: "test",
  features: { chat: true, web_search: true, personal_workspace: false, review_workspace: false, ingestion_worker: false },
  time_budget_seconds: { search: 4, evidence: 12, generation: 18, total: 20 },
};

const session: SessionPayload = {
  principal: { id: null, auth_mode: "anonymous", authenticated: false, profile: null, is_admin: false, review_namespace: null },
  capabilities: { public_chat: true, server_history: false, personal_academic: false, knowledge_review: false, production_publish: false },
  csrf_token: "csrf",
};

test("chat renders verified complete segments, citations, and saves anonymous history locally", async () => {
  const user = userEvent.setup();
  render(<Tooltip.Provider><ChatWorkspace config={config} session={session} /></Tooltip.Provider>);
  const input = screen.getByRole("textbox", { name: "向小蜗提问" });
  await user.type(input, "公开校历是什么？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(screen.getByText(/已核验的完整回答/)).toBeInTheDocument());
  expect(document.querySelector(".markdown-body sup.cite")).not.toBeNull();
  expect(screen.queryByText("核验证据")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /来源 1/ }));
  expect(screen.getByRole("link", { name: /教务处来源/ })).toBeInTheDocument();
  expect(putLocalConversation).toHaveBeenCalledTimes(1);
});

test("public starter prompt fills and focuses the composer without sending", async () => {
  const user = userEvent.setup();
  render(<Tooltip.Provider><ChatWorkspace config={config} session={session} /></Tooltip.Provider>);

  await user.click(screen.getByRole("button", { name: /本学期校历/ }));
  const input = screen.getByRole("textbox", { name: "向小蜗提问" });
  expect(input).toHaveValue("请查询本学期校历安排，并列出开学、考试周和重要教学节点。");
  await waitFor(() => expect(input).toHaveFocus());
  expect(screen.queryByText(/已核验的完整回答/)).not.toBeInTheDocument();
});

test("personal academic capability selects valid personal starter prompts", async () => {
  const personalSession: SessionPayload = {
    ...session,
    principal: {
      ...session.principal,
      id: "PB25111691",
      auth_mode: "demo",
      authenticated: true,
      profile: { id: "PB25111691", name: "测试", major: "计算机科学与技术", grade: "2025级" },
    },
    capabilities: { ...session.capabilities, server_history: true, personal_academic: true },
  };
  render(<Tooltip.Provider><ChatWorkspace config={config} session={personalSession} /></Tooltip.Provider>);

  // 今日弹窗（Radix 模态）打开时背景为 aria-hidden，先关闭再校验启动提示
  await userEvent.setup().click(screen.getByRole("button", { name: "知道了" }));
  expect(screen.getByRole("button", { name: /今日课表/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /本周日程/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /独立冲突检查/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /本学期校历/ })).not.toBeInTheDocument();
});

test("seeded academic question suppresses starters until a new conversation", async () => {
  const user = userEvent.setup();
  const onSeedConsumed = vi.fn();
  render(
    <Tooltip.Provider>
      <ChatWorkspace config={config} session={session} seededQuestion="请点评离散数学" onSeedConsumed={onSeedConsumed} />
    </Tooltip.Provider>,
  );

  expect(screen.getByRole("textbox", { name: "向小蜗提问" })).toHaveValue("请点评离散数学");
  expect(screen.queryByRole("heading", { name: "常见问题" })).not.toBeInTheDocument();
  expect(onSeedConsumed).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("button", { name: "新建对话" }));
  expect(screen.getByRole("heading", { name: "常见问题" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "向小蜗提问" })).toHaveValue("");
});

test("structured cards dedupe by content: identical pushes collapse, same-shape distinct cards both survive", async () => {
  const cardA = {
    title: "课程搜索结果", source_tool: "search_courses",
    columns: ["课程码", "课程"], rows: [["A1", "甲课"]],
  };
  // 同标题 / 同工具 / 同行数，但内容不同 —— 不能被去重键误删
  const cardB = {
    title: "课程搜索结果", source_tool: "search_courses",
    columns: ["课程码", "课程"], rows: [["B1", "乙课"]],
  };
  vi.mocked(streamRunEvents).mockImplementationOnce(async (_path, options) => {
    // 同一张卡推两次：模拟「act 节点实时推 + run 结束重推」
    options.onEvent({ id: 1, run_id: "run-fixture", type: "data.table", at: "2026-08-27T00:00:00Z", data: cardA });
    options.onEvent({ id: 2, run_id: "run-fixture", type: "data.table", at: "2026-08-27T00:00:01Z", data: cardA });
    options.onEvent({ id: 3, run_id: "run-fixture", type: "data.table", at: "2026-08-27T00:00:02Z", data: cardB });
    options.onEvent({
      id: 4, run_id: "run-fixture", type: "answer.segment", at: "2026-08-27T00:00:03Z",
      data: { segment_id: "seg-cards", markdown: "两张卡的回答。", claim_ids: [] },
    });
    options.onEvent({
      id: 5, run_id: "run-fixture", type: "answer.completed", at: "2026-08-27T00:00:04Z",
      data: { answer_id: "answer-cards", claims: [], sources: [], limitations: [], terminal_reason: "local_answer" },
    });
  });

  const user = userEvent.setup();
  render(<Tooltip.Provider><ChatWorkspace config={config} session={session} /></Tooltip.Provider>);
  await user.type(screen.getByRole("textbox", { name: "向小蜗提问" }), "组合数学");
  await user.click(screen.getByRole("button", { name: "发送" }));

  await waitFor(() => expect(screen.getByText(/两张卡的回答/)).toBeInTheDocument());
  // 三张卡里第一张重复 → 折叠为 2 张（甲课 + 乙课），且乙课未被同形去重键误删
  expect(document.querySelectorAll(".structured-table")).toHaveLength(2);
  expect(screen.getByText("甲课")).toBeInTheDocument();
  expect(screen.getByText("乙课")).toBeInTheDocument();
});

test("structured table scores and give-scores carry semantic cell levels", async () => {
  vi.mocked(streamRunEvents).mockImplementationOnce(async (_path, options) => {
    options.onEvent({
      id: 1, run_id: "run-fixture", type: "data.table", at: "2026-08-27T00:00:00Z",
      data: {
        title: "评课对比", source_tool: "analyze_teacher",
        columns: ["班级/教师", "评分", "给分"],
        rows: [["邵帅", "9.3", "超好"], ["姜晓枫", "6.4", "杀手"], ["吕敏", "6.5", "一般"]],
      },
    });
    options.onEvent({
      id: 2, run_id: "run-fixture", type: "answer.segment", at: "2026-08-27T00:00:01Z",
      data: { segment_id: "seg-level", markdown: "看表。", claim_ids: [] },
    });
    options.onEvent({
      id: 3, run_id: "run-fixture", type: "answer.completed", at: "2026-08-27T00:00:02Z",
      data: { answer_id: "a", claims: [], sources: [], limitations: [], terminal_reason: "local_answer" },
    });
  });

  const user = userEvent.setup();
  render(<Tooltip.Provider><ChatWorkspace config={config} session={session} /></Tooltip.Provider>);
  await user.type(screen.getByRole("textbox", { name: "向小蜗提问" }), "离散数学各班老师");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(screen.getByText("看表。")).toBeInTheDocument());

  // 高分层：9.3 评分 + 超好给分
  expect(document.querySelectorAll('.structured-table td[data-level="high"]')).toHaveLength(2);
  // 低分层：杀手给分（6.4 / 6.5 评分与「一般」属中档，CSS 不着色）
  expect(document.querySelectorAll('.structured-table td[data-level="low"]')).toHaveLength(1);
  // 只有评分/给分两列带分级：2 列 × 3 行 = 6；其余列不参与
  expect(document.querySelectorAll(".structured-table td[data-level]")).toHaveLength(6);
  expect(document.querySelectorAll('.structured-table td[data-level="mid"]')).toHaveLength(3);
  // 内容不得被改写
  expect(screen.getByText("9.3")).toBeInTheDocument();
  expect(screen.getByText("杀手")).toBeInTheDocument();
});
