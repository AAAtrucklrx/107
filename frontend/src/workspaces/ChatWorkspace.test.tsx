import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import * as Tooltip from "@radix-ui/react-tooltip";
import { ChatWorkspace } from "./ChatWorkspace";
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

test("personal academic capability selects valid personal starter prompts", () => {
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
