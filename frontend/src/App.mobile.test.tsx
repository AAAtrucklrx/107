import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { SessionPayload } from "./types";

const native = vi.hoisted(() => ({
  cleanup: vi.fn(async () => {}), open: vi.fn(async () => {}), close: vi.fn(async () => {}), showStatus: vi.fn(async () => {}),
  attach: vi.fn(async () => () => {}), getCookies: vi.fn(), stored: null as string | null,
}));
vi.mock("./lib/mobile", async () => {
  const { MobileTokenStore } = await import("../../mobile/src/login/token");
  return {
    isNativeMobile: () => true,
    isMobileOnline: () => true,
    mobileApiBase: () => "http://127.0.0.1:8000/api/v1",
    attachMobileLifecycle: native.attach,
    SchoolLogin: native,
    schoolCookies: { getCookies: native.getCookies },
    mobileTokens: new MobileTokenStore({ get: async () => ({ value: native.stored }), set: async ({ value }) => { native.stored = value; }, remove: async () => { native.stored = null; } }),
  };
});
vi.mock("./workspaces/ChatWorkspace", () => ({ ChatWorkspace: () => <div>公共问答已就绪</div> }));
const anon: SessionPayload = {
  principal: { id: null, auth_mode: "anonymous", authenticated: false, profile: null, is_admin: false, review_namespace: null },
  capabilities: { public_chat: true, server_history: false, personal_academic: false, knowledge_review: false, production_publish: false }, csrf_token: "",
};
beforeEach(() => {
  native.open.mockClear(); native.cleanup.mockReset(); native.cleanup.mockResolvedValue(undefined); native.stored = null;
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("fetch", async (url: string) => new Response(JSON.stringify(
    url.endsWith("/bootstrap") ? { access_token: "b".repeat(43), token_type: "Bearer", expires_in: 3600 } :
    url.endsWith("/session") ? anon : { environment: "development", auth_mode: "anonymous", version: "test", features: {}, time_budget_seconds: { total: 150 } }
  )));
});
test("native startup requires an explicit login or anonymous choice", async () => {
  render(<App />);
  expect(await screen.findByRole("button", { name: "用学校账号登录" })).toBeInTheDocument();
  expect(native.open).not.toHaveBeenCalled();
  expect(screen.queryByText("公共问答已就绪")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "先匿名体验" }));
  expect(await screen.findByText("公共问答已就绪")).toBeInTheDocument();
  expect(native.stored).toBeNull();
});
test("explicit mobile login opens the native school view, not browser CAS", async () => {
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "用学校账号登录" }));
  await waitFor(() => expect(native.open).toHaveBeenCalledOnce());
  expect(window.location.pathname).toBe("/");
});
test("an expired native session returns to login", async () => {
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "先匿名体验" }));
  await screen.findByText("公共问答已就绪");
  act(() => { window.dispatchEvent(new Event("xiaowo:mobile-auth-expired")); });
  expect(await screen.findByRole("button", { name: "用学校账号登录" })).toBeInTheDocument();
  expect(await screen.findByText(/登录已过期/)).toBeInTheDocument();
});
test("unsupported school cleanup explains the update while anonymous chat remains usable", async () => {
  native.cleanup.mockRejectedValue(Object.assign(new Error("unsupported"), { code: "WEBVIEW_UPDATE_REQUIRED" }));
  render(<App />);
  await waitFor(() => expect(screen.getByRole("button", { name: "先匿名体验" })).toBeEnabled());
  expect(screen.getByRole("button", { name: "用学校账号登录" })).toBeDisabled();
  expect(screen.getByText(/更新.*WebView/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "先匿名体验" }));
  expect(await screen.findByText("公共问答已就绪")).toBeInTheDocument();
  expect(screen.getByText(/更新.*WebView/)).toBeInTheDocument();
});

test("native profile reconnect keeps using the school controller rather than the browser cookie panel", async () => {
  native.stored = "c".repeat(43);
  const connected: SessionPayload = {
    ...anon, jw_session: true, jw_identity: { student_id: "TEST-MOBILE", name: "移动同学" },
    principal: { ...anon.principal, id: "TEST-MOBILE", auth_mode: "cas", authenticated: true },
    capabilities: { ...anon.capabilities, personal_academic: true },
  };
  vi.stubGlobal("fetch", async (url: string) => new Response(JSON.stringify(url.endsWith("/session") ? connected : { environment: "development", auth_mode: "anonymous", version: "test", features: {}, time_budget_seconds: { total: 150 } })));
  window.history.replaceState({}, "", "/profile");
  render(<App />);
  await screen.findByRole("heading", { name: "移动同学" });
  await userEvent.click(screen.getByRole("button", { name: /重新连接教务/ }));
  expect(await screen.findByRole("heading", { name: "重新连接学校账号" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "断开教务连接" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "教务会话 JSON" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "返回小蛙助手" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "用学校账号登录" }));
  await waitFor(() => expect(native.open).toHaveBeenCalledOnce());
});

test("an existing native session can leave reconnect management while offline", async () => {
  native.stored = "d".repeat(43);
  let networkChanged!: (online: boolean) => void;
  native.attach.mockImplementationOnce((async (_controller: unknown, onNetwork: typeof networkChanged) => {
    networkChanged = onNetwork;
    return () => {};
  }) as never);
  const connected: SessionPayload = {
    ...anon, jw_session: true, jw_identity: { student_id: "TEST-OFFLINE", name: "离线同学" },
    principal: { ...anon.principal, id: "TEST-OFFLINE", auth_mode: "cas", authenticated: true },
    capabilities: { ...anon.capabilities, personal_academic: true },
  };
  vi.stubGlobal("fetch", async (url: string) => new Response(JSON.stringify(url.endsWith("/session") ? connected : { environment: "development", auth_mode: "anonymous", version: "test", features: {}, time_budget_seconds: { total: 150 } })));
  window.history.replaceState({}, "", "/profile");
  render(<App />);
  await screen.findByRole("heading", { name: "离线同学" });
  act(() => networkChanged(false));
  await userEvent.click(screen.getByRole("button", { name: /重新连接教务/ }));
  expect(await screen.findByRole("heading", { name: "重新连接学校账号" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "用学校账号登录" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "返回小蛙助手" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "返回小蛙助手" }));
  expect(await screen.findByRole("heading", { name: "离线同学" })).toBeInTheDocument();
  expect(native.open).not.toHaveBeenCalled();
  expect(native.stored).toBe("d".repeat(43));
});
