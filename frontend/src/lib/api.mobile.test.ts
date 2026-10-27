import { afterEach, beforeEach, expect, test, vi } from "vitest";
const mobile = vi.hoisted(() => ({ native: true, online: true, token: "a".repeat(43), clear: vi.fn(async () => {}) }));
vi.mock("./mobile", () => ({
  isNativeMobile: () => mobile.native,
  mobileApiBase: () => "http://127.0.0.1:8000/api/v1",
  isMobileOnline: () => mobile.online,
  mobileTokens: { get value() { return mobile.token; }, clear: mobile.clear },
}));
import { apiGet, apiMutation, streamRunEvents } from "./api";

beforeEach(() => { mobile.native = true; mobile.online = true; mobile.token = "a".repeat(43); mobile.clear.mockReset(); mobile.clear.mockResolvedValue(undefined); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

test("mobile GET and mutation use bearer, omit cookies and never put credentials in URL", async () => {
  const requests: Array<{ url: string; init: RequestInit }> = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => { requests.push({ url, init }); return new Response("{}", { status: 200 }); });
  await apiGet("/auth/session");
  await apiMutation("/chat/runs", "browser-csrf", { method: "POST", body: "{}" });
  expect(requests.map((r) => r.url)).toEqual(["http://127.0.0.1:8000/api/v1/auth/session", "http://127.0.0.1:8000/api/v1/chat/runs"]);
  for (const { init } of requests) {
    expect(new Headers(init.headers).get("Authorization")).toBe(`Bearer ${mobile.token}`);
    expect(new Headers(init.headers).has("X-CSRF-Token")).toBe(false);
    expect(init.credentials).toBe("omit");
  }
});
test("mobile SSE stays streaming with bearer and Last-Event-ID", async () => {
  const received: unknown[] = [];
  let headers = new Headers();
  vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
    headers = new Headers(init.headers);
    return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('data: {"id":8,"type":"answer.delta","data":{"delta":"好"}}\n\n')); controller.close(); } }));
  });
  await streamRunEvents("/chat/runs/test/events", { signal: new AbortController().signal, afterEventId: 7, onEvent: (event) => received.push(event) });
  expect(headers.get("Authorization")).toBe(`Bearer ${mobile.token}`);
  expect(headers.get("Last-Event-ID")).toBe("7");
  expect(received).toEqual([{ id: 8, type: "answer.delta", data: { delta: "好" } }]);
});
test("web requests keep relative URLs and cookie/CSRF behavior", async () => {
  mobile.native = false;
  const fetcher = vi.fn(async (_url: string, _init: RequestInit) => new Response("{}"));
  vi.stubGlobal("fetch", fetcher);
  await apiMutation("/auth/logout", "csrf", { method: "POST", body: "{}" });
  expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/logout", expect.objectContaining({ credentials: "include" }));
  const init = fetcher.mock.calls[0][1] as RequestInit;
  expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf");
  expect(new Headers(init.headers).has("Authorization")).toBe(false);
});
test("offline errors are friendly and preserve credentials", async () => {
  mobile.online = false;
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(apiGet("/auth/session")).rejects.toMatchObject({ code: "OFFLINE", message: expect.stringMatching(/离线/) });
  expect(fetcher).not.toHaveBeenCalled();
  expect(mobile.clear).not.toHaveBeenCalled();
});
test("401 clears the active bearer and emits reauthentication without fallback cookies", async () => {
  const expired = vi.fn(); window.addEventListener("xiaowo:mobile-auth-expired", expired);
  vi.stubGlobal("fetch", async () => new Response('{"error":{"code":"AUTH_REQUIRED","message":"登录凭据已失效，请重新登录。"}}', { status: 401 }));
  await expect(apiGet("/auth/session")).rejects.toMatchObject({ status: 401, message: expect.stringMatching(/重新登录/) });
  expect(mobile.clear).toHaveBeenCalledOnce();
  expect(expired).toHaveBeenCalledOnce();
  window.removeEventListener("xiaowo:mobile-auth-expired", expired);
});
test("a personal-data 401 preserves the server login explanation", async () => {
  const received: string[] = [];
  const expired = (event: Event) => { received.push((event as CustomEvent<{ message: string }>).detail?.message); };
  window.addEventListener("xiaowo:mobile-auth-expired", expired);
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: { code: "AUTH_REQUIRED", message: "个人学业问题需要先登录。" } }), { status: 401 }));
  await expect(apiMutation("/chat/runs", "", { method: "POST", body: "{}" })).rejects.toMatchObject({ message: "个人学业问题需要先登录。" });
  expect(received).toEqual(["个人学业问题需要先登录。"]);
  window.removeEventListener("xiaowo:mobile-auth-expired", expired);
});
test("401 invalidation is announced before a slow secure clear can race a new login", async () => {
  let finish!: () => void;
  const cleared = new Promise<void>((resolve) => { finish = resolve; });
  mobile.clear.mockImplementation(() => cleared);
  const observed: string[] = [];
  const expired = () => { observed.push(mobile.token); };
  window.addEventListener("xiaowo:mobile-auth-expired", expired);
  vi.stubGlobal("fetch", async () => new Response("{}", { status: 401 }));
  const result = expect(apiGet("/auth/session")).rejects.toMatchObject({ status: 401 });
  await vi.waitFor(() => expect(mobile.clear).toHaveBeenCalledOnce());
  mobile.token = "new-session";
  finish();
  await result;
  expect(observed).toEqual(["a".repeat(43)]);
  window.removeEventListener("xiaowo:mobile-auth-expired", expired);
});
test("network failure and request timeout do not discard the saved login", async () => {
  vi.stubGlobal("fetch", async () => { throw new TypeError("Failed to fetch"); });
  await expect(apiGet("/auth/session")).rejects.toMatchObject({ code: "NETWORK_ERROR", message: expect.stringMatching(/网络|连接/) });
  vi.useFakeTimers();
  vi.stubGlobal("fetch", (_url: string, init: RequestInit) => new Promise((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))));
  const request = expect(apiGet("/auth/session")).rejects.toMatchObject({ code: "REQUEST_TIMEOUT", message: expect.stringMatching(/超时/) });
  await vi.advanceTimersByTimeAsync(30001);
  await request;
  expect(mobile.clear).not.toHaveBeenCalled();
});
