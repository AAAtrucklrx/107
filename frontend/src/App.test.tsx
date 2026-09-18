import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import type { PublicConfig, SessionPayload } from "./types";
import { App } from "./App";

const bootstrapMock = vi.hoisted(() => vi.fn());
const apiMutationMock = vi.hoisted(() => vi.fn());

vi.mock("./lib/api", () => ({
  bootstrap: bootstrapMock,
  apiMutation: apiMutationMock,
}));

vi.mock("./workspaces/ChatWorkspace", () => ({
  ChatWorkspace: () => <div>用户问答工作区</div>,
}));
vi.mock("./workspaces/AcademicWorkspace", () => ({
  AcademicWorkspace: () => <div>学业工作区</div>,
}));
vi.mock("./workspaces/CampusWorkspace", () => ({
  CampusWorkspace: () => <div>校园服务工作区</div>,
}));
vi.mock("./workspaces/AdminToolsWorkspace", () => ({
  AdminToolsWorkspace: () => <div>工具审核工作区</div>,
}));
vi.mock("./workspaces/ReviewWorkspace", () => ({
  ReviewWorkspace: () => <div>知识审核工作区</div>,
}));

const config: PublicConfig = {
  environment: "competition",
  auth_mode: "demo",
  version: "test",
  features: {
    chat: true,
    web_search: false,
    personal_workspace: true,
    review_workspace: true,
    ingestion_worker: false,
  },
  time_budget_seconds: { search: 1, evidence: 1, generation: 1, total: 3 },
};

const adminSession: SessionPayload = {
  principal: {
    id: "PB25111691",
    auth_mode: "demo",
    authenticated: true,
    profile: { id: "PB25111691", name: "测试管理员", major: "计算机科学与技术", grade: "2025级" },
    is_admin: true,
    review_namespace: "demo",
  },
  capabilities: {
    public_chat: true,
    server_history: true,
    personal_academic: true,
    knowledge_review: true,
    production_publish: false,
  },
  csrf_token: "csrf-demo",
};

beforeEach(() => {
  bootstrapMock.mockReset();
  apiMutationMock.mockReset();
  window.history.replaceState({}, "", "/");
});

test("opens knowledge review on the bare administrator route（P2-5）", async () => {
  // 2026-09-17 改：裸 /admin 默认进「知识审核」（管理主战场），原来默认落常空的「工具审核」
  window.history.replaceState({}, "", "/admin");
  bootstrapMock.mockResolvedValue({ config, session: adminSession });
  render(<App />);

  expect(await screen.findByText("知识审核工作区")).toBeInTheDocument();
  expect(screen.getAllByText("管理后台")).toHaveLength(2);
  expect(window.location.pathname).toBe("/admin/knowledge");
});

test("keeps the explicit tool review route", async () => {
  window.history.replaceState({}, "", "/admin/tools");
  bootstrapMock.mockResolvedValue({ config, session: adminSession });
  render(<App />);

  expect(await screen.findByText("工具审核工作区")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/admin/tools");
});

test("redirects the legacy review route to knowledge review", async () => {
  window.history.replaceState({}, "", "/review");
  bootstrapMock.mockResolvedValue({ config, session: adminSession });
  render(<App />);

  expect(await screen.findByText("知识审核工作区")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/admin/knowledge");
});

test("keeps non-administrators out of administrator routes", async () => {
  window.history.replaceState({}, "", "/admin");
  bootstrapMock.mockResolvedValue({
    config,
    session: {
      ...adminSession,
      principal: { ...adminSession.principal, is_admin: false, review_namespace: null },
      capabilities: { ...adminSession.capabilities, knowledge_review: false },
    },
  });
  render(<App />);

  expect(await screen.findByText("用户问答工作区")).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/"));
  expect(screen.queryByText("管理后台")).not.toBeInTheDocument();
});


test("账号菜单进管理后台直达知识审核（P2-5 入口回归）", async () => {
  // 教训：上一次只测了 URL 解析（/admin → 知识审核），但**用户真正的入口是账号菜单**，
  // 而菜单写死 navigateAdmin("tools")，于是"看着没生效"。测试必须走入口。
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  bootstrapMock.mockResolvedValue({ config, session: adminSession });
  render(<App />);

  // 账号菜单要等会话（bootstrap）加载完才渲染 —— 先等主工作区
  await screen.findByText("用户问答工作区");
  const trigger = await waitFor(() => {
    const node = document.querySelector(
      ".account-trigger:not(.account-trigger--compact)",
    ) as HTMLElement | null;
    expect(node).toBeTruthy();
    return node as HTMLElement;
  });
  await user.click(trigger);

  const items = await screen.findAllByRole("menuitem", { name: /管理后台/ });
  await user.click(items[0]);

  expect(await screen.findByText("知识审核工作区")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/admin/knowledge");
  expect(screen.queryByText("工具审核工作区")).toBeNull();
});
