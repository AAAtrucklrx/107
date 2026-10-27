import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppShell } from "../components/AppShell";
import type { PublicConfig, SessionPayload } from "../types";

vi.mock("../mascot/MascotLayer", () => ({ MascotLayer: () => <div data-testid="mascot" /> }));
const originalWidth = window.innerWidth;
afterEach(() => { Object.defineProperty(window, "innerWidth", { configurable: true, value: originalWidth }); });
const session: SessionPayload = {
  principal: { id: null, auth_mode: "anonymous", authenticated: false, profile: null, is_admin: false, review_namespace: null },
  capabilities: { personal_academic: false, public_chat: true, server_history: false, knowledge_review: false, production_publish: false }, csrf_token: "",
};
const config = { environment: "development", auth_mode: "anonymous", features: {} } as PublicConfig;
const noop = async () => {};

test("phone has exactly three tabs including academic before login and changes form on resize", () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });
  const { container } = render(<AppShell config={config} session={session} workspace="chat" onWorkspaceChange={() => {}} theme="light" onThemeToggle={() => {}} onDemoLogin={noop} onOpenLogin={() => {}} onLogout={noop} onDemoReset={noop} onOpenAdmin={() => {}} busy={false}>正文</AppShell>);
  expect(container.querySelector(".app-scaffold")).toHaveAttribute("data-form", "phone");
  expect(screen.getByRole("navigation", { name: "底部导航" }).querySelectorAll("button")).toHaveLength(3);
  expect(screen.getByRole("button", { name: "学业" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "我的" })).toBeInTheDocument();
  for (const [width, form] of [[640, "tablet"], [768, "tablet"], [1024, "tablet"], [1025, "desktop"], [1440, "desktop"]] as const) {
    act(() => { Object.defineProperty(window, "innerWidth", { configurable: true, value: width }); window.dispatchEvent(new Event("resize")); });
    expect(container.querySelector(".app-scaffold")).toHaveAttribute("data-form", form);
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "底部导航" })).not.toBeInTheDocument();
  }
  expect(screen.queryByTestId("mascot")).not.toBeInTheDocument();
});
