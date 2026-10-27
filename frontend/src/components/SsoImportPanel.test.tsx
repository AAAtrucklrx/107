import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SessionProvider, useSession } from "../session/SessionProvider";
import { SsoImportPanel } from "./SsoImportPanel";
import type { SessionPayload } from "../types";

const mocks = vi.hoisted(() => ({ bootstrap: vi.fn(), mutation: vi.fn() }));
vi.mock("../lib/api", () => ({ bootstrap: mocks.bootstrap, apiMutation: mocks.mutation }));
const config = { environment: "development", auth_mode: "anonymous", features: {}, version: "test" };
const anonymous: SessionPayload = {
  principal: { id: null, auth_mode: "anonymous", authenticated: false, profile: null, is_admin: false, review_namespace: null },
  capabilities: { public_chat: true, server_history: false, personal_academic: false, knowledge_review: false, production_publish: false }, csrf_token: "test-csrf",
};
const verified: SessionPayload = { ...anonymous, jw_session: true, jw_identity: { name: "验证同学", student_id: "TEST001" }, principal: { ...anonymous.principal, id: "TEST001", auth_mode: "cas", authenticated: true } };
function Consumer() { const { session } = useSession(); return <><span>{session?.jw_identity?.name || "公共身份"}</span><SsoImportPanel /></>; }
const navigate = () => {};
beforeEach(() => { mocks.bootstrap.mockReset(); mocks.mutation.mockReset().mockResolvedValue({}); });
afterEach(() => { delete (window as unknown as { xiaowoDesktop?: unknown }).xiaowoDesktop; });

test("manual school import refreshes the single shared session and disconnect clears identity", async () => {
  mocks.bootstrap.mockResolvedValueOnce({ config, session: anonymous }).mockResolvedValueOnce({ config, session: verified }).mockResolvedValue({ config, session: anonymous });
  render(<SessionProvider onNavigate={navigate}><Consumer /></SessionProvider>);
  await waitFor(() => expect(mocks.bootstrap).toHaveBeenCalledOnce());
  await userEvent.click(screen.getByRole("button", { name: /已有登录态/ }));
  await userEvent.click(screen.getByRole("textbox", { name: "教务会话 JSON" }));
  await userEvent.paste('{"cookies":[{"name":"fixture","value":"synthetic"}]}');
  await userEvent.click(screen.getByRole("button", { name: "导入" }));
  expect(await screen.findByText("验证同学")).toBeInTheDocument();
  expect(mocks.mutation).toHaveBeenCalledWith("/auth/sso_cookie", "test-csrf", expect.objectContaining({ method: "POST" }));
  await userEvent.click(screen.getByRole("button", { name: "断开教务连接" }));
  expect(await screen.findByText("公共身份")).toBeInTheDocument();
  expect(mocks.mutation).toHaveBeenLastCalledWith("/auth/sso_cookie", "test-csrf", expect.objectContaining({ method: "DELETE" }));
});

test("legacy Electron bridge is subscribed once across StrictMode and detached React views stay inactive", async () => {
  let notify!: (payload: { ok: boolean }) => void;
  const subscribe = vi.fn((listener: typeof notify) => { notify = listener; return {}; });
  (window as unknown as { xiaowoDesktop: unknown }).xiaowoDesktop = { onJwSession: subscribe };
  mocks.bootstrap.mockResolvedValue({ config, session: anonymous });
  const view = render(<StrictMode><SessionProvider onNavigate={navigate}><Consumer /></SessionProvider></StrictMode>);
  await screen.findByText("公共身份");
  expect(subscribe).toHaveBeenCalledOnce();
  mocks.bootstrap.mockResolvedValue({ config, session: verified });
  await act(async () => { notify({ ok: true }); });
  expect(await screen.findByText("验证同学")).toBeInTheDocument();
  view.unmount();
  const calls = mocks.bootstrap.mock.calls.length;
  await act(async () => { notify({ ok: true }); });
  expect(mocks.bootstrap).toHaveBeenCalledTimes(calls);
});
