import { render, screen, waitFor } from "@testing-library/react";
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

test("opens the isolated administrator route on tool review by default", async () => {
  window.history.replaceState({}, "", "/admin");
  bootstrapMock.mockResolvedValue({ config, session: adminSession });
  render(<App />);

  expect(await screen.findByText("工具审核工作区")).toBeInTheDocument();
  expect(screen.getAllByText("管理后台")).toHaveLength(2);
  expect(window.location.pathname).toBe("/admin");
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
