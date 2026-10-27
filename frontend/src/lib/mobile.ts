import { Capacitor, CapacitorCookies, registerPlugin, type PluginListenerHandle } from "@capacitor/core";
import { App as CapacitorApp } from "@capacitor/app";
import { Network } from "@capacitor/network";
import type { MobileLoginController, SchoolLoginBridge } from "../../../mobile/src/login/controller";
import { MobileTokenStore, type SecureTokenBridge } from "../../../mobile/src/login/token";
import { mobileDeepLinkPath, resolveMobileApiBase } from "./mobileConfig";

interface SchoolPlugin extends SchoolLoginBridge {
  addListener(event: "landed", listener: (payload: { url: string }) => void): Promise<PluginListenerHandle>;
  addListener(event: "cancelled" | "retry", listener: () => void): Promise<PluginListenerHandle>;
}
export const SchoolLogin = registerPlugin<SchoolPlugin>("SchoolLogin");
const SecureToken = registerPlugin<SecureTokenBridge>("SecureToken");
export const mobileTokens = new MobileTokenStore(SecureToken);
export const schoolCookies = CapacitorCookies;
export const isNativeMobile = (): boolean => Capacitor.isNativePlatform();
export const mobileApiBase = (): string => resolveMobileApiBase(import.meta.env.VITE_MOBILE_API_BASE, import.meta.env.MODE, import.meta.env.VITE_MOBILE_DEBUG);
let connected = true;
export const isMobileOnline = (): boolean => connected;

export async function attachMobileLifecycle(
  controller: MobileLoginController,
  onNetwork: (online: boolean) => void,
  navigate: (path: string) => void,
): Promise<() => void> {
  const handles: PluginListenerHandle[] = [];
  try {
    handles.push(await SchoolLogin.addListener("landed", (event) => { void controller.handleLanded(event); }));
    handles.push(await SchoolLogin.addListener("cancelled", () => { void controller.cancel(); }));
    handles.push(await SchoolLogin.addListener("retry", () => { void controller.retry(); }));
    handles.push(await Network.addListener("networkStatusChange", (status) => { connected = status.connected; onNetwork(connected); }));
    handles.push(await CapacitorApp.addListener("appUrlOpen", ({ url }) => {
      const path = mobileDeepLinkPath(url);
      if (path !== null) navigate(path);
    }));
    handles.push(await CapacitorApp.addListener("backButton", () => {
      if (["opening", "authorizing", "welcome"].includes(controller.snapshot.phase)) void controller.cancel();
      else if (window.location.pathname !== "/") navigate("/");
      else void CapacitorApp.minimizeApp();
    }));
    connected = (await Network.getStatus()).connected;
    onNetwork(connected);
    const launch = await CapacitorApp.getLaunchUrl();
    if (launch) {
      const path = mobileDeepLinkPath(launch.url);
      if (path !== null) navigate(path);
    }
    return () => { handles.forEach((handle) => { void handle.remove(); }); };
  } catch (error) {
    await Promise.allSettled(handles.map((handle) => handle.remove()));
    throw error;
  }
}
