import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { apiMutation, bootstrap, mobileAuthRequest } from "../lib/api";
import { attachMobileLifecycle, isNativeMobile, mobileTokens, SchoolLogin, schoolCookies } from "../lib/mobile";
import { MobileLoginController, type LoginSnapshot } from "../../../mobile/src/login/controller";
import type { PublicConfig, SessionPayload, Theme } from "../types";
import { useAppearance } from "../appearance/AppearanceProvider";

interface DesktopBridge { onJwSession?: (callback: (payload: { ok: boolean }) => void) => unknown }
const desktopSubscriptions = new WeakMap<DesktopBridge, { callbacks: Set<() => void>; dispose?: () => void }>();

/** Older Electron bridges cannot unsubscribe; keep one bridge listener and release React subscribers. */
function subscribeDesktopSession(callback: () => void): () => void {
  const bridge = (window as unknown as { xiaowoDesktop?: DesktopBridge }).xiaowoDesktop;
  if (!bridge?.onJwSession) return () => {};
  let subscription = desktopSubscriptions.get(bridge);
  if (!subscription) {
    subscription = { callbacks: new Set() };
    desktopSubscriptions.set(bridge, subscription);
    const current = subscription;
    const dispose = bridge.onJwSession((payload) => { if (payload.ok) current.callbacks.forEach((listener) => listener()); });
    if (typeof dispose === "function") subscription.dispose = dispose as () => void;
  }
  subscription.callbacks.add(callback);
  return () => {
    subscription.callbacks.delete(callback);
    if (!subscription.callbacks.size && subscription.dispose) {
      subscription.dispose();
      desktopSubscriptions.delete(bridge);
    }
  };
}

interface SessionContextValue {
  config: PublicConfig | null;
  session: SessionPayload | null;
  mobile: boolean;
  mobileState?: LoginSnapshot;
  offline: boolean;
  error: string | null;
  busy: boolean;
  theme: Theme;
  toggleTheme: () => void;
  loginOpen: boolean;
  openLogin: () => void;
  closeLogin: () => void;
  reload: () => Promise<void>;
  demoLogin: () => Promise<boolean>;
  logout: () => Promise<boolean>;
  demoReset: () => Promise<boolean>;
  demoResetVersion: number;
  startSchoolLogin: () => void;
  enterAnonymous: () => void;
  retryMobile: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function useOptionalSession() { return useContext(SessionContext); }
export function useSession(): SessionContextValue {
  const value = useOptionalSession();
  if (!value) throw new Error("useSession requires SessionProvider");
  return value;
}

export function SessionProvider({ children, onNavigate }: { children: ReactNode; onNavigate: (path: string) => void }) {
  const { style } = useAppearance();
  const [mobileController] = useState(() => isNativeMobile() ? new MobileLoginController({ tokens: mobileTokens, school: SchoolLogin, cookies: schoolCookies, request: mobileAuthRequest }) : null);
  const [mobileState, setMobileState] = useState(mobileController?.snapshot);
  const [offline, setOffline] = useState(false);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [demoResetVersion, setDemoResetVersion] = useState(0);
  const generation = useRef(0);
  const [theme, setTheme] = useState<Theme>(() => {
    try { return localStorage.getItem("xiaowo-theme") === "dark" ? "dark" : "light"; }
    catch { return "light"; }
  });

  const reload = useCallback(async () => {
    const epoch = ++generation.current;
    const token = mobileTokens.value;
    const current = () => epoch === generation.current && (!mobileController || (mobileController.snapshot.phase === "ready" && token === mobileTokens.value));
    setError(null);
    try {
      if (mobileController && mobileController.snapshot.phase !== "ready") {
        await mobileController.initialize();
        return;
      }
      const result = await bootstrap();
      if (!current()) return;
      setConfig(result.config);
      setSession(result.session);
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : "无法连接小蛙助手服务。");
    }
  }, [mobileController]);

  useEffect(() => {
    if (!mobileController) {
      void reload();
      const onNetwork = () => setOffline(!navigator.onLine);
      onNetwork();
      window.addEventListener("online", onNetwork);
      window.addEventListener("offline", onNetwork);
      return () => { generation.current++; window.removeEventListener("online", onNetwork); window.removeEventListener("offline", onNetwork); };
    }
    let disposed = false;
    let detach: (() => void) | undefined;
    const unsubscribe = mobileController.subscribe(() => {
      if (disposed) return;
      setMobileState(mobileController.snapshot);
      if (mobileController.snapshot.phase === "ready") {
        setLoginOpen(false);
        setSession(mobileController.snapshot.session ?? null);
        void reload();
      } else if (mobileController.snapshot.phase === "login") {
        generation.current++;
        setSession(null);
      }
    });
    const expired = (event: Event) => { void mobileController.expired((event as CustomEvent<{ message?: string }>).detail?.message).catch(() => { if (!disposed) setError("安全凭据清理失败，请重启后重新登录。"); }); };
    window.addEventListener("xiaowo:mobile-auth-expired", expired);
    document.documentElement.dataset.mobile = "true";
    void attachMobileLifecycle(mobileController, (online) => { if (!disposed) setOffline(!online); }, (path) => { if (!disposed) onNavigate(path); }).then((cleanup) => {
      if (disposed) { cleanup(); return; }
      detach = cleanup;
      return mobileController.initialize();
    }).catch(() => { if (!disposed) setMobileState({ phase: "login", error: "移动端安全组件暂不可用，请重启 App 后重试。" }); });
    return () => {
      disposed = true;
      generation.current++;
      unsubscribe();
      detach?.();
      window.removeEventListener("xiaowo:mobile-auth-expired", expired);
      delete document.documentElement.dataset.mobile;
    };
  }, [mobileController, onNavigate, reload]);

  useEffect(() => {
    let active = true;
    const unsubscribe = subscribeDesktopSession(() => { void reload().then(() => { if (active) setLoginOpen(false); }); });
    return () => { active = false; unsubscribe(); };
  }, [reload]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("xiaowo-theme", theme); } catch { /* Theme still works in private storage mode. */ }
    document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute("content", style === "frog" ? (theme === "dark" ? "#1c1b16" : "#faf7ed") : (theme === "dark" ? "#111722" : "#f6f8fc"));
  }, [theme, style]);

  const demoLogin = useCallback(async () => {
    if (!session) return false;
    generation.current++;
    setBusy(true); setError(null);
    try {
      const next = await apiMutation<SessionPayload>("/auth/demo", session.csrf_token, { method: "POST", body: "{}" });
      setSession(next); setLoginOpen(false);
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "演示登录失败。"); return false; }
    finally { setBusy(false); }
  }, [session]);

  const logout = useCallback(async () => {
    generation.current++;
    setBusy(true); setError(null);
    try {
      if (mobileController) await mobileController.logout();
      else if (session) {
        await apiMutation("/auth/logout", session.csrf_token, { method: "POST", body: "{}" });
        await reload();
      }
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "退出登录失败。"); return false; }
    finally { setBusy(false); }
  }, [mobileController, reload, session]);

  const demoReset = useCallback(async () => {
    if (!session || session.principal.auth_mode !== "demo") return false;
    setBusy(true); setError(null);
    try {
      await apiMutation("/auth/demo/reset", session.csrf_token, { method: "POST", body: "{}" });
      setDemoResetVersion((value) => value + 1);
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "恢复演示数据失败。"); return false; }
    finally { setBusy(false); }
  }, [session]);

  return <SessionContext.Provider value={{ config, session, mobile: !!mobileController, mobileState, offline, error, busy,
    theme, toggleTheme: () => setTheme((value) => value === "light" ? "dark" : "light"),
    loginOpen, openLogin: () => setLoginOpen(true), closeLogin: () => setLoginOpen(false), reload, demoLogin, logout, demoReset, demoResetVersion,
    startSchoolLogin: () => { void mobileController?.start(); },
    enterAnonymous: () => { if (mobileController?.snapshot.phase === "ready") setLoginOpen(false); else void mobileController?.anonymous(); },
    retryMobile: () => { void mobileController?.initialize(); },
  }}>{children}</SessionContext.Provider>;
}
