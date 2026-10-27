/// <reference types="node" />
import { describe, expect, test, vi } from "vitest";
import { MobileLoginController } from "../../../mobile/src/login/controller";
import { MobileTokenStore } from "../../../mobile/src/login/token";
import { createPkce } from "../../../mobile/src/login/pkce";
import { webcrypto } from "node:crypto";

const TOKEN = "a".repeat(43);
const ANON = "b".repeat(43);
const session = { principal: { authenticated: true }, jw_session: true, jw_identity: { name: "测试同学", student_id: "TEST001" } };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
function harness() {
  let persisted: string | null = null;
  const native = {
    get: async () => ({ value: persisted }),
    set: async ({ value }: { value: string }) => { persisted = value; },
    remove: async () => { persisted = null; },
  };
  const tokens = new MobileTokenStore(native);
  const school = { open: vi.fn(async () => {}), close: vi.fn(async () => {}), cleanup: vi.fn(async () => {}), showStatus: vi.fn(async () => {}) };
  const cookies = { getCookies: vi.fn(async ({ url }: { url: string }): Promise<Record<string, string>> => url.includes("id.ustc.edu.cn") ? { SOURCEID_TGC: "test-sso", tracking: "drop" } : { SESSION: "test-jw" }) };
  const request = vi.fn(async (path: string, _init?: RequestInit, _token?: string | null): Promise<unknown> => {
    if (path.endsWith("/authorize")) return { code: "one-time", expires_in: 60 };
    if (path.endsWith("/exchange")) return { access_token: TOKEN, token_type: "Bearer", expires_in: 43200 };
    if (path.endsWith("/bootstrap")) return { access_token: ANON, token_type: "Bearer", expires_in: 3600 };
    if (path.endsWith("/session")) return _token === ANON ? { ...session, principal: { authenticated: false }, jw_session: false, jw_identity: {} } : session;
    return {};
  });
  const controller = new MobileLoginController({ tokens, school, cookies, request: request as never,
    pkce: async () => ({ verifier: "v".repeat(43), challenge: "challenge" }), delay: async () => {} });
  return { controller, tokens, school, cookies, request, native, persisted: () => persisted };
}

describe("mobile login lifecycle", () => {
  test("unsupported WebView cleanup blocks school login but preserves anonymous access", async () => {
    const h = harness();
    h.school.cleanup.mockRejectedValue(Object.assign(new Error("unsupported cleanup"), { code: "WEBVIEW_UPDATE_REQUIRED" }));
    await h.controller.initialize();
    expect(h.controller.snapshot.schoolLoginError).toMatch(/更新.*WebView/);
    await h.controller.start();
    expect(h.school.open).not.toHaveBeenCalled();
    await h.controller.anonymous();
    expect(h.controller.snapshot.phase).toBe("ready");
    expect(h.tokens.value).toBe(ANON);
    expect(h.controller.snapshot.schoolLoginError).toMatch(/更新.*WebView/);
  });
  test("unsupported WebView cleanup does not discard or block an already valid token", async () => {
    const h = harness();
    await h.tokens.authenticate(TOKEN);
    h.school.cleanup.mockRejectedValue(Object.assign(new Error("unsupported cleanup"), { code: "WEBVIEW_UPDATE_REQUIRED" }));
    await h.controller.initialize();
    expect(h.controller.snapshot.phase).toBe("ready");
    expect(h.persisted()).toBe(TOKEN);
    expect(h.controller.snapshot.schoolLoginError).toMatch(/更新.*WebView/);
  });
  test("startup cleans school data and presents login without opening school or creating anonymous credentials", async () => {
    const h = harness();
    await h.controller.initialize();
    expect(h.controller.snapshot.phase).toBe("login");
    expect(h.school.cleanup).toHaveBeenCalledOnce();
    expect(h.school.open).not.toHaveBeenCalled();
    expect(h.request).not.toHaveBeenCalled();
  });
  test("success reads both domains, requires verified identity, and persists only the bearer", async () => {
    const h = harness();
    await h.controller.start();
    await h.controller.handleLanded({ url: "https://jw.ustc.edu.cn/home" });
    expect(h.controller.snapshot.phase).toBe("ready");
    expect(h.persisted()).toBe(TOKEN);
    const body = JSON.parse(String(h.request.mock.calls.find(([p]) => p.endsWith("authorize"))?.[1]?.body));
    expect(body.cookies.map((c: { domain: string }) => c.domain)).toEqual(["id.ustc.edu.cn", "jw.ustc.edu.cn"]);
    expect(JSON.stringify(body)).not.toContain("tracking");
    expect(body.code_challenge_method).toBe("S256");
    expect(h.school.showStatus).toHaveBeenCalledWith({ state: "success", message: "✅ 欢迎，测试同学" });
    expect(h.school.close).toHaveBeenCalledOnce();
  });
  test("missing SSO TGC keeps school open and retry can succeed", async () => {
    const h = harness();
    h.cookies.getCookies.mockResolvedValueOnce({});
    await h.controller.start();
    await h.controller.handleLanded({ url: "https://jw.ustc.edu.cn/" });
    expect(h.controller.snapshot.phase).toBe("login");
    expect(h.controller.snapshot.error).toMatch(/统一认证|TGC/);
    expect(h.school.close).not.toHaveBeenCalled();
    expect(h.persisted()).toBeNull();
    await h.controller.retry();
    expect(h.controller.snapshot.phase).toBe("ready");
  });
  test("a lookalike landing host cannot trigger cookie extraction", async () => {
    const h = harness();
    await h.controller.start();
    await h.controller.handleLanded({ url: "https://jw.ustc.edu.cn.attacker.invalid/" });
    expect(h.cookies.getCookies).not.toHaveBeenCalled();
    expect(h.persisted()).toBeNull();
  });
  test.each(["/ucas-sso/login?ticket=test", "/UCAS-SSO/login/redirect", "/auth/ucas-sso/login", "/auth/ucas-sso%2Flogin", "/cas/login", "/login"])("the JW SSO redirect endpoint %s is not a completed login", async (path) => {
    const h = harness();
    await h.controller.start();
    await h.controller.handleLanded({ url: `https://jw.ustc.edu.cn${path}` });
    expect(h.cookies.getCookies).not.toHaveBeenCalled();
    expect(h.request).not.toHaveBeenCalled();
  });
  test("welcome remains visible for two seconds and waits for native cleanup before ready", async () => {
    const h = harness();
    const hold = deferred<void>();
    const closing = deferred<void>();
    const delay = vi.fn(async (_milliseconds: number) => hold.promise);
    const controller = new MobileLoginController({ tokens: h.tokens, school: h.school, cookies: h.cookies, request: h.request as never,
      pkce: async () => ({ verifier: "v".repeat(43), challenge: "challenge" }), delay });
    h.school.close.mockImplementation(() => closing.promise);
    await controller.start();
    const login = controller.handleLanded({ url: "https://jw.ustc.edu.cn/home" });
    await vi.waitFor(() => expect(controller.snapshot.phase).toBe("welcome"));
    expect(delay).toHaveBeenCalledWith(2000);
    expect(h.school.close).not.toHaveBeenCalled();
    hold.resolve();
    await vi.waitFor(() => expect(h.school.close).toHaveBeenCalledOnce());
    expect(controller.snapshot.phase).toBe("welcome");
    closing.resolve();
    await login;
    expect(controller.snapshot.phase).toBe("ready");
  });
  test("cancelling while exchange is pending rejects a late token", async () => {
    const h = harness();
    const late = deferred<unknown>();
    h.request.mockImplementation(async (path) => path.endsWith("authorize") ? { code: "one-time" } : late.promise);
    await h.controller.start();
    const login = h.controller.handleLanded({ url: "https://jw.ustc.edu.cn/" });
    await vi.waitFor(() => expect(h.request.mock.calls.some(([p]) => p.endsWith("exchange"))).toBe(true));
    await h.controller.cancel();
    late.resolve({ access_token: TOKEN, token_type: "Bearer", expires_in: 43200 });
    await login;
    expect(h.persisted()).toBeNull();
    expect(h.tokens.value).toBeNull();
    expect(h.controller.snapshot.phase).toBe("login");
  });
  test("cancelling during Keystore write removes the late persisted token", async () => {
    const h = harness();
    const writing = deferred<void>();
    const original = h.native.set;
    h.native.set = async (value) => { await writing.promise; await original(value); };
    await h.controller.start();
    const login = h.controller.handleLanded({ url: "https://jw.ustc.edu.cn/" });
    await vi.waitFor(() => expect(h.request.mock.calls.some(([p]) => p.endsWith("session"))).toBe(true));
    const cancel = h.controller.cancel();
    writing.resolve();
    await Promise.all([login, cancel]);
    expect(h.persisted()).toBeNull();
    expect(h.tokens.value).toBeNull();
  });
  test("anonymous access is opt-in and never persisted", async () => {
    const h = harness();
    await h.controller.anonymous();
    expect(h.controller.snapshot.phase).toBe("ready");
    expect(h.tokens.value).toBe(ANON);
    expect(h.persisted()).toBeNull();
  });
  test("a cancelled anonymous storage operation cannot erase a newer school login", async () => {
    const h = harness();
    const clearing = deferred<void>();
    const remove = h.native.remove;
    let first = true;
    h.native.remove = vi.fn(async () => {
      if (first) { first = false; await clearing.promise; }
      await remove();
    });
    const anonymous = h.controller.anonymous();
    await vi.waitFor(() => expect(h.native.remove).toHaveBeenCalledOnce());
    await h.controller.cancel();
    await h.controller.start();
    const login = h.controller.handleLanded({ url: "https://jw.ustc.edu.cn/home" });
    await vi.waitFor(() => expect(h.request.mock.calls.some(([path, , token]) => path.endsWith("/session") && token === TOKEN)).toBe(true));
    clearing.resolve();
    await Promise.all([anonymous, login]);
    expect(h.controller.snapshot.phase).toBe("ready");
    expect(h.tokens.value).toBe(TOKEN);
    expect(h.persisted()).toBe(TOKEN);
  });
  test("offline restore preserves secure token; a confirmed 401 removes it", async () => {
    const h = harness();
    await h.tokens.authenticate(TOKEN);
    h.request.mockRejectedValueOnce(new Error("当前离线"));
    await h.controller.initialize();
    expect(h.persisted()).toBe(TOKEN);
    expect(h.controller.snapshot.error).toMatch(/离线/);
    h.request.mockRejectedValueOnce(Object.assign(new Error("登录已过期"), { status: 401 }));
    await h.controller.initialize();
    expect(h.persisted()).toBeNull();
    expect(h.controller.snapshot.phase).toBe("login");
  });
  test("offline logout clears device credentials and reports unconfirmed remote revocation", async () => {
    const h = harness();
    await h.tokens.authenticate(TOKEN);
    h.request.mockRejectedValue(new Error("当前离线"));
    await h.controller.logout();
    expect(h.tokens.value).toBeNull();
    expect(h.persisted()).toBeNull();
    expect(h.controller.snapshot.error).toMatch(/服务器.*未确认/);
    expect(h.school.cleanup).toHaveBeenCalledOnce();
    expect(h.request.mock.calls.map(([p]) => p)).toEqual(["/auth/sso_cookie", "/auth/logout"]);
  });
  test("late logout completion cannot replace a newer login attempt", async () => {
    const h = harness();
    await h.tokens.authenticate(TOKEN);
    const revoked = deferred<unknown>();
    h.request.mockImplementation(() => revoked.promise);
    const logout = h.controller.logout();
    await vi.waitFor(() => expect(h.request).toHaveBeenCalled());
    await h.controller.start();
    revoked.resolve({});
    await logout;
    expect(h.controller.snapshot.phase).toBe("opening");
  });
  test("a failed secure write never leaves a reusable credential", async () => {
    const h = harness();
    const save = h.native.set;
    h.native.set = async (value) => { await save(value); throw new Error("Keystore write failed"); };
    await h.controller.start();
    await h.controller.handleLanded({ url: "https://jw.ustc.edu.cn/home" });
    expect(h.tokens.value).toBeNull();
    expect(h.persisted()).toBeNull();
    expect(h.controller.snapshot.phase).toBe("login");
    expect(h.school.close).not.toHaveBeenCalled();
  });
});

test("PKCE uses random 32-byte verifier and SHA-256 base64url challenge", async () => {
  const result = await createPkce(webcrypto as unknown as Crypto);
  expect(result.verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(result.challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
  const digest = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(result.verifier));
  expect(result.challenge).toBe(Buffer.from(digest).toString("base64url"));
  expect((await createPkce(webcrypto as unknown as Crypto)).verifier).not.toBe(result.verifier);
});
