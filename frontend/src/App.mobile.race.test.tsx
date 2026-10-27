import { act, render, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { MobileLoginController } from "../../mobile/src/login/controller";
import type { SessionPayload } from "./types";

const state = vi.hoisted(() => ({ stored: "a".repeat(43) as string | null, controller: null as MobileLoginController | null }));
vi.mock("./lib/mobile", async () => {
  const { MobileTokenStore } = await import("../../mobile/src/login/token");
  return {
    isNativeMobile: () => true, isMobileOnline: () => true,
    mobileApiBase: () => "http://127.0.0.1:8000/api/v1",
    attachMobileLifecycle: async (controller: MobileLoginController) => { state.controller = controller; return () => {}; },
    SchoolLogin: { cleanup: async () => {}, open: async () => {}, close: async () => {}, showStatus: async () => {} },
    schoolCookies: { getCookies: async () => ({}) },
    mobileTokens: new MobileTokenStore({ get: async () => ({ value: state.stored }), set: async ({ value }) => { state.stored = value; }, remove: async () => { state.stored = null; } }),
  };
});
vi.mock("./workspaces/ChatWorkspace", () => ({ ChatWorkspace: () => <div>聊天</div> }));
import { App } from "./App";

function session(id: string): SessionPayload {
  return {
    principal: { id, auth_mode: "cas", authenticated: true, profile: { id, name: id, major: "测试", grade: "2026" }, is_admin: false, review_namespace: null },
    capabilities: { public_chat: true, server_history: true, personal_academic: true, knowledge_review: false, production_publish: false },
    csrf_token: "", jw_session: true, jw_identity: { student_id: id, name: id },
  };
}

test("a late bootstrap from a logged-out account cannot replace the newer identity", async () => {
  let oldResponse!: (response: Response) => void;
  const oldRequest = new Promise<Response>((resolve) => { oldResponse = resolve; });
  let sessions = 0;
  const json = (data: unknown) => new Response(JSON.stringify(data));
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("/session")) {
      sessions += 1;
      if (sessions === 1) return json(session("ACCOUNT_A"));
      if (sessions === 2) return oldRequest;
      return json(session("ACCOUNT_B"));
    }
    return json({ environment: "development", auth_mode: "anonymous", version: "test", features: {}, time_budget_seconds: { total: 150 } });
  });
  const { container } = render(<App />);
  await waitFor(() => expect(sessions).toBe(2));
  await act(async () => { await state.controller!.logout(); });
  state.stored = "b".repeat(43);
  await act(async () => { await state.controller!.initialize(); });
  await waitFor(() => expect(container.querySelector(".scaffold-profile-label")).toHaveTextContent("ACCOUNT_B"));
  await act(async () => { oldResponse(json(session("ACCOUNT_A"))); });
  await waitFor(() => expect(container.querySelector(".scaffold-profile-label")).toHaveTextContent("ACCOUNT_B"));
  vi.unstubAllGlobals();
});
