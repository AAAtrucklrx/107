import { render, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { AppShell } from "./AppShell";
import type { PublicConfig, SessionPayload } from "../types";

test("account identity uses verified school name and ID", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-24T10:30:00Z"));
  const session: SessionPayload = {
    principal: { id: "TEST001", auth_mode: "cas", authenticated: true, profile: { name: "旧画像", id: "TEST001", major: "测试", grade: "2025" }, is_admin: false, review_namespace: null },
    capabilities: { personal_academic: true, public_chat: true, server_history: true, knowledge_review: false, production_publish: false }, csrf_token: "",
    jw_session: true, jw_identity: { name: "已验证同学", student_id: "TEST001" },
  };
  const config = { environment: "development", auth_mode: "cas", features: {} } as PublicConfig;
  const noop = async () => {};
  const { container } = render(<AppShell mobile config={config} session={session} workspace="chat" onWorkspaceChange={() => {}} theme="light" onThemeToggle={() => {}} onDemoLogin={noop} onOpenLogin={() => {}} onLogout={noop} onDemoReset={noop} onOpenAdmin={() => {}} busy={false}>正文</AppShell>);
  const bottom = within(container.querySelector(".account-trigger")! as HTMLElement);
  expect(bottom.getByText("已验证同学")).toBeInTheDocument();
  expect(bottom.getByText("TEST001")).toBeInTheDocument();
  expect(bottom.getByText("已连接教务")).toBeInTheDocument();
  expect(container.querySelector(".scaffold-header")?.textContent).not.toContain("晚上好");
  vi.useRealTimers();
});
