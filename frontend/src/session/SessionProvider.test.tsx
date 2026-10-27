import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { SessionProvider, useSession } from "./SessionProvider";
import type { SessionPayload } from "../types";

const mocks = vi.hoisted(() => ({ bootstrap: vi.fn(), mutation: vi.fn() }));
vi.mock("../lib/api", () => ({ bootstrap: mocks.bootstrap, apiMutation: mocks.mutation }));
const config = { environment: "development", auth_mode: "anonymous", features: {}, version: "test" };
const anonymous: SessionPayload = {
  principal: { id: null, auth_mode: "anonymous", authenticated: false, profile: null, is_admin: false, review_namespace: null },
  capabilities: { public_chat: true, server_history: false, personal_academic: false, knowledge_review: false, production_publish: false }, csrf_token: "test-csrf",
};
const account = { ...anonymous, jw_identity: { name: "旧账户" }, principal: { ...anonymous.principal, authenticated: true } };
function Consumer() {
  const { session, reload, logout } = useSession();
  return <><output>{session ? session.principal.authenticated ? session.jw_identity?.name : "匿名" : "加载中"}</output><button onClick={() => void reload()}>刷新</button><button onClick={() => void logout()}>退出</button></>;
}

test("a stale browser bootstrap cannot resurrect identity after logout", async () => {
  let completeOld!: (value: unknown) => void;
  const oldRequest = new Promise((resolve) => { completeOld = resolve; });
  mocks.bootstrap.mockResolvedValueOnce({ config, session: account }).mockReturnValueOnce(oldRequest).mockResolvedValue({ config, session: anonymous });
  mocks.mutation.mockResolvedValue({});
  render(<SessionProvider onNavigate={() => {}}><Consumer /></SessionProvider>);
  await screen.findByText("旧账户");
  await userEvent.click(screen.getByRole("button", { name: "刷新" }));
  await userEvent.click(screen.getByRole("button", { name: "退出" }));
  await screen.findByText("匿名");
  await act(async () => { completeOld({ config, session: account }); });
  expect(screen.getByText("匿名")).toBeInTheDocument();
  expect(screen.queryByText("旧账户")).not.toBeInTheDocument();
});
