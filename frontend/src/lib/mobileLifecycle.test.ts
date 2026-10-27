import { beforeEach, expect, test, vi } from "vitest";
const bridge = vi.hoisted(() => ({
  callbacks: new Map<string, (payload?: never) => void>(),
  removed: [] as string[],
  minimize: vi.fn(async () => {}),
  network: { connected: true },
  launch: undefined as { url: string } | undefined,
}));
vi.mock("@capacitor/core", () => ({
  Capacitor: { isNativePlatform: () => true }, CapacitorCookies: {},
  registerPlugin: () => ({ addListener: async (name: string, callback: (payload?: never) => void) => { bridge.callbacks.set(name, callback); return { remove: async () => { bridge.removed.push(name); } }; } }),
}));
vi.mock("@capacitor/app", () => ({ App: {
  addListener: async (name: string, callback: (payload?: never) => void) => { bridge.callbacks.set(name, callback); return { remove: async () => { bridge.removed.push(name); } }; },
  minimizeApp: bridge.minimize, getLaunchUrl: async () => bridge.launch,
} }));
vi.mock("@capacitor/network", () => ({ Network: {
  addListener: async (name: string, callback: (payload?: never) => void) => { bridge.callbacks.set(name, callback); return { remove: async () => { bridge.removed.push(name); } }; },
  getStatus: async () => bridge.network,
} }));
import { attachMobileLifecycle, isMobileOnline } from "./mobile";
import type { MobileLoginController } from "../../../mobile/src/login/controller";

beforeEach(() => { bridge.callbacks.clear(); bridge.removed.length = 0; bridge.minimize.mockClear(); bridge.launch = undefined; bridge.network = { connected: true }; window.history.replaceState({}, "", "/"); });
test("native network, deep link and return events update only supported app state", async () => {
  const controller = { snapshot: { phase: "ready" }, handleLanded: vi.fn(), cancel: vi.fn(), retry: vi.fn() };
  const online = vi.fn(); const navigate = vi.fn();
  const detach = await attachMobileLifecycle(controller as unknown as MobileLoginController, online, navigate);
  bridge.callbacks.get("networkStatusChange")?.({ connected: false } as never);
  expect(isMobileOnline()).toBe(false);
  expect(online).toHaveBeenLastCalledWith(false);
  bridge.callbacks.get("appUrlOpen")?.({ url: "xiaowo://app/academic" } as never);
  expect(navigate).toHaveBeenLastCalledWith("/academic");
  bridge.callbacks.get("appUrlOpen")?.({ url: "xiaowo://app/admin?access_token=test" } as never);
  expect(navigate).toHaveBeenCalledTimes(1);
  window.history.replaceState({}, "", "/academic");
  bridge.callbacks.get("backButton")?.();
  expect(navigate).toHaveBeenLastCalledWith("/");
  window.history.replaceState({}, "", "/");
  bridge.callbacks.get("backButton")?.();
  expect(bridge.minimize).toHaveBeenCalledOnce();
  detach();
  expect(bridge.removed.sort()).toEqual(["appUrlOpen", "backButton", "cancelled", "landed", "networkStatusChange", "retry"]);
});
test("school events feed the controller and back cancels an active login", async () => {
  const controller = { snapshot: { phase: "authorizing" }, handleLanded: vi.fn(), cancel: vi.fn(), retry: vi.fn() };
  const detach = await attachMobileLifecycle(controller as unknown as MobileLoginController, () => {}, () => {});
  bridge.callbacks.get("landed")?.({ url: "https://jw.ustc.edu.cn/home" } as never);
  expect(controller.handleLanded).toHaveBeenCalledWith({ url: "https://jw.ustc.edu.cn/home" });
  bridge.callbacks.get("retry")?.();
  expect(controller.retry).toHaveBeenCalledOnce();
  bridge.callbacks.get("backButton")?.();
  expect(controller.cancel).toHaveBeenCalledOnce();
  expect(bridge.minimize).not.toHaveBeenCalled();
  detach();
});
