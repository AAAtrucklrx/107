import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { App } from "../App";
import type { SseEnvelope } from "../types";

const streamRunEventsMock = vi.hoisted(() => vi.fn());

const anonymous = {
  principal: { id: null, auth_mode: "anonymous", authenticated: false, profile: null, is_admin: false, review_namespace: null },
  capabilities: { public_chat: true, server_history: false, personal_academic: false, knowledge_review: false, production_publish: false }, csrf_token: "test-csrf",
};
vi.mock("../lib/api", () => ({
  bootstrap: async () => ({ config: { environment: "development", auth_mode: "anonymous", features: { chat: true }, version: "test", time_budget_seconds: { total: 150 } }, session: anonymous }),
  apiGet: async () => ({ items: [] }),
  apiMutation: async () => ({ run_id: "run-tab-switch", conversation_id: null, requested_mode: "auto", effective_mode: "auto", events_url: "/api/v1/chat/runs/run-tab-switch/events" }),
  apiDelete: vi.fn(),
  streamRunEvents: streamRunEventsMock,
}));
vi.mock("../lib/anonymousHistory", () => ({ listLocalConversations: async () => [], putLocalConversation: vi.fn(), deleteLocalConversation: vi.fn(), clearLocalConversations: vi.fn() }));

test("the application shell supplies tooltip context to the real chat workspace", async () => {
  window.history.replaceState({}, "", "/");
  render(<App />);
  expect(await screen.findByRole("textbox", { name: "向小蛙助手提问" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /奶家人，今天谁陪你/ })).toBeInTheDocument();
});

test("switching to profile and back preserves the unfinished chat draft", async () => {
  window.history.replaceState({}, "", "/");
  render(<App />);
  const input = await screen.findByRole("textbox", { name: "向小蛙助手提问" });
  await userEvent.type(input, "还没发出的选课问题");
  await userEvent.click(screen.getByRole("button", { name: "我的" }));
  expect(await screen.findByRole("heading", { name: "我的" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "问答" }));
  expect(await screen.findByRole("textbox", { name: "向小蛙助手提问" })).toHaveValue("还没发出的选课问题");
});


test("an answer keeps streaming across a profile visit and is available on return", async () => {
  window.history.replaceState({}, "", "/");
  let streamOptions: { signal: AbortSignal; onEvent: (event: SseEnvelope) => void } | undefined;
  let finishStream: () => void = () => {};
  streamRunEventsMock.mockImplementationOnce(async (_path, options) => {
    streamOptions = options;
    options.onEvent({ id: 1, run_id: "run-tab-switch", type: "answer.segment", at: "2026-10-26T00:00:00Z", data: { segment_id: "first", markdown: "先整理问题。", claim_ids: [] } });
    await new Promise<void>((resolve) => { finishStream = resolve; });
  });
  const user = userEvent.setup();
  render(<App />);
  await user.type(await screen.findByRole("textbox", { name: "向小蛙助手提问" }), "图书馆周末开门吗？");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(streamOptions).toBeDefined());
  expect(screen.getByRole("button", { name: "停止回答" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "我的" }));
  expect(await screen.findByRole("heading", { name: "我的" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "向小蛙助手提问" })).not.toBeInTheDocument();
  expect(streamOptions?.signal.aborted).toBe(false);
  await act(async () => {
    streamOptions?.onEvent({ id: 2, run_id: "run-tab-switch", type: "answer.segment", at: "2026-10-26T00:00:01Z", data: { segment_id: "first", markdown: "在后台完成的图书馆回答。", claim_ids: [] } });
    streamOptions?.onEvent({ id: 3, run_id: "run-tab-switch", type: "answer.completed", at: "2026-10-26T00:00:02Z", data: { answer_id: "answer-tab-switch", claims: [], sources: [], limitations: [], terminal_reason: "local_answer" } });
    finishStream();
  });
  expect(streamOptions?.signal.aborted).toBe(false);

  await user.click(screen.getByRole("button", { name: "问答" }));
  expect(await screen.findByText("在后台完成的图书馆回答。")).toBeVisible();
  expect(screen.getByText("图书馆周末开门吗？")).toBeVisible();
  expect(screen.queryByRole("button", { name: "停止回答" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
});
