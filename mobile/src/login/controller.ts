import type { SessionPayload } from "../../../frontend/src/types";
import { createPkce } from "./pkce";
import { MobileTokenStore } from "./token";

export interface SchoolLoginBridge {
  open(): Promise<void>;
  close(): Promise<void>;
  cleanup(): Promise<void>;
  showStatus(options: { state: "loading" | "error" | "success"; message: string }): Promise<void>;
}
export interface LoginSnapshot {
  phase: "booting" | "login" | "opening" | "authorizing" | "welcome" | "ready";
  session?: SessionPayload;
  error?: string;
  schoolLoginError?: string;
}
export interface LoginDependencies {
  school: SchoolLoginBridge;
  cookies: { getCookies(options: { url: string }): Promise<Record<string, string>> };
  tokens: MobileTokenStore;
  request<T>(path: string, init?: RequestInit, token?: string | null): Promise<T>;
  pkce?: typeof createPkce;
  delay?: (milliseconds: number) => Promise<void>;
}
const SSO = "https://id.ustc.edu.cn/cas/login";
const JW = "https://jw.ustc.edu.cn/";
const allowed: Record<string, readonly string[]> = {
  "id.ustc.edu.cn": ["SOURCEID_TGC", "SVRNAME", "rememberToken", "deviceID", "device"],
  "jw.ustc.edu.cn": ["SESSION", "SVRNAME", "rememberToken", "deviceID", "device"],
};
function message(error: unknown): string {
  return error instanceof Error ? error.message : "登录暂未完成，请重试。";
}
function unauthorized(error: unknown): boolean {
  return !!error && typeof error === "object" && "status" in error && error.status === 401;
}

export class MobileLoginController {
  snapshot: LoginSnapshot = { phase: "booting" };
  private listeners = new Set<() => void>();
  private epoch = 0;
  private abort?: AbortController;
  private windowOpen = false;
  private landedUrl: string | null = null;
  private pending = false;
  private schoolLoginError?: string;
  constructor(private readonly dependencies: LoginDependencies) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private update(snapshot: LoginSnapshot): void {
    this.snapshot = { ...snapshot, schoolLoginError: this.schoolLoginError };
    this.listeners.forEach((listener) => listener());
  }
  private next(): number {
    this.epoch += 1;
    this.abort?.abort();
    this.abort = new AbortController();
    this.pending = false;
    return this.epoch;
  }
  private async error(error: unknown, epoch: number): Promise<void> {
    if (epoch !== this.epoch) return;
    this.update({ phase: "login", error: message(error) });
    if (this.windowOpen) {
      await this.dependencies.school.showStatus({ state: "error", message: message(error) }).catch(() => {});
    }
  }

  private async cleanupSchoolData(epoch: number): Promise<boolean> {
    try {
      await this.dependencies.school.cleanup();
      if (epoch === this.epoch) this.schoolLoginError = undefined;
      return true;
    } catch (error) {
      if (epoch === this.epoch) {
        const requiresUpdate = !!error && typeof error === "object" && "code" in error && error.code === "WEBVIEW_UPDATE_REQUIRED";
        this.schoolLoginError = requiresUpdate
          ? "请更新 Android System WebView（或 Chrome）后重新检查；学校登录暂不可用，仍可匿名体验或使用已有会话。"
          : "学校登录数据尚未清理完成，请重新检查后再登录。仍可匿名体验或使用已有会话。";
      }
      return false;
    }
  }

  async initialize(): Promise<void> {
    const epoch = this.next();
    this.windowOpen = false;
    this.landedUrl = null;
    this.update({ phase: "booting" });
    try {
      await this.cleanupSchoolData(epoch);
      if (epoch !== this.epoch) return;
      const token = await this.dependencies.tokens.restore();
      if (epoch !== this.epoch) return;
      if (!token) { this.update({ phase: "login" }); return; }
      const session = await this.dependencies.request<SessionPayload>("/auth/session", { signal: this.abort?.signal }, token);
      if (epoch !== this.epoch) return;
      if (!session.principal.authenticated || !session.jw_session) {
        await this.dependencies.tokens.clear();
        if (epoch !== this.epoch) return;
        this.update({ phase: "login", error: "教务连接已过期，请重新登录。" });
        return;
      }
      this.update({ phase: "ready", session });
    } catch (error) {
      if (epoch !== this.epoch) return;
      if (unauthorized(error)) await this.dependencies.tokens.clear();
      await this.error(error, epoch);
    }
  }

  async start(): Promise<void> {
    if (this.schoolLoginError) return;
    const epoch = this.next();
    this.landedUrl = null;
    this.update({ phase: "opening" });
    try {
      await this.dependencies.school.open();
      if (epoch !== this.epoch) return;
      this.windowOpen = true;
    } catch (error) { await this.error(error, epoch); }
  }

  async handleLanded(event: { url?: string }): Promise<void> {
    if (!this.windowOpen || this.pending) return;
    try {
      const url = new URL(event.url || "");
      if (url.protocol !== "https:" || url.hostname !== "jw.ustc.edu.cn" || url.port || url.username || url.password) return;
      const path = decodeURIComponent(url.pathname).toLowerCase();
      if (path.includes("ucas-sso/login") || /^\/(?:cas\/)?login\/?$/.test(path)) return;
      this.landedUrl = url.href;
    } catch { return; }
    await this.exchange();
  }
  async retry(): Promise<void> {
    if (this.windowOpen && this.landedUrl) await this.exchange();
    else await this.start();
  }
  private async exchange(): Promise<void> {
    if (this.pending) return;
    const epoch = this.epoch;
    const current = () => epoch === this.epoch && this.windowOpen;
    const { school, cookies, tokens, request } = this.dependencies;
    this.pending = true;
    this.update({ phase: "authorizing" });
    let issued: string | null = null;
    try {
      await school.showStatus({ state: "loading", message: "正在安全连接教务…" });
      const [sso, jw] = await Promise.all([cookies.getCookies({ url: SSO }), cookies.getCookies({ url: JW })]);
      if (!current()) return;
      if (!sso.SOURCEID_TGC?.trim()) throw new Error("尚未取得统一认证会话，请在学校页面完成登录后重试。");
      const selected = [["id.ustc.edu.cn", sso], ["jw.ustc.edu.cn", jw]] as const;
      const payload = selected.flatMap(([domain, entries]) => Object.entries(entries)
        .filter(([name, value]) => allowed[domain].includes(name) && value.trim())
        .map(([name, value]) => ({ domain, name, value, path: "/" })));
      const pkce = await (this.dependencies.pkce ?? createPkce)();
      if (!current()) return;
      const grant = await request<{ code: string }>("/auth/mobile/authorize", {
        method: "POST", signal: this.abort?.signal,
        body: JSON.stringify({ cookies: payload, code_challenge: pkce.challenge, code_challenge_method: "S256" }),
      }, null);
      if (!current()) return;
      const result = await request<{ access_token: string }>("/auth/mobile/exchange", {
        method: "POST", signal: this.abort?.signal, body: JSON.stringify({ code: grant.code, code_verifier: pkce.verifier }),
      }, null);
      issued = result.access_token;
      if (!current()) return;
      const session = await request<SessionPayload>("/auth/session", { signal: this.abort?.signal }, issued);
      if (!current()) return;
      if (!session.principal.authenticated || !session.jw_session || !session.jw_identity?.name || !session.jw_identity.student_id) {
        throw new Error("未能验证真实教务身份，请在学校页面重新登录。");
      }
      if (!await tokens.authenticate(issued, current) || !current()) return;
      this.update({ phase: "welcome", session });
      await school.showStatus({ state: "success", message: `✅ 欢迎，${session.jw_identity.name}` });
      await (this.dependencies.delay ?? ((ms) => new Promise<void>((resolve) => setTimeout(resolve, ms))))(2000);
      if (!current()) return;
      await school.close();
      if (epoch !== this.epoch) return;
      this.windowOpen = false;
      issued = null;
      this.update({ phase: "ready", session });
    } catch (error) {
      if (issued && tokens.value === issued) await tokens.clear();
      await this.error(error, epoch);
    } finally {
      // A token returned after cancellation must also be revoked when reachable.
      if (issued) await request("/auth/logout", { method: "POST", body: "{}" }, issued).catch(() => {});
      if (epoch === this.epoch) this.pending = false;
    }
  }

  async cancel(): Promise<void> {
    const hadWelcome = this.snapshot.phase === "welcome";
    const epoch = this.next();
    this.windowOpen = false;
    this.landedUrl = null;
    this.update({ phase: "booting" });
    try {
      if (hadWelcome) await this.dependencies.tokens.clear();
      await this.cleanupSchoolData(epoch);
      if (epoch === this.epoch) this.update({ phase: "login" });
    } catch (error) { await this.error(error, epoch); }
  }

  async anonymous(): Promise<void> {
    const epoch = this.next();
    this.update({ phase: "booting" });
    try {
      await this.cleanupSchoolData(epoch);
      if (epoch !== this.epoch) return;
      const result = await this.dependencies.request<{ access_token: string }>("/auth/mobile/bootstrap", { method: "POST", body: "{}", signal: this.abort?.signal }, null);
      if (epoch !== this.epoch) return;
      const session = await this.dependencies.request<SessionPayload>("/auth/session", { signal: this.abort?.signal }, result.access_token);
      if (epoch !== this.epoch) return;
      if (!await this.dependencies.tokens.anonymous(result.access_token, () => epoch === this.epoch)) return;
      this.update({ phase: "ready", session });
    } catch (error) { await this.error(error, epoch); }
  }

  async logout(): Promise<void> {
    const epoch = this.next();
    this.windowOpen = false;
    this.landedUrl = null;
    const token = this.dependencies.tokens.value;
    this.update({ phase: "booting" });
    const results = await Promise.allSettled([this.dependencies.tokens.clear(), this.cleanupSchoolData(epoch)]);
    if (epoch === this.epoch) this.update({ phase: "login" });
    let remoteConfirmed = true;
    for (const path of ["/auth/sso_cookie", "/auth/logout"]) {
      if (!token) break;
      try { await this.dependencies.request(path, { method: path.endsWith("sso_cookie") ? "DELETE" : "POST", body: "{}" }, token); }
      catch (error) { if (!unauthorized(error)) remoteConfirmed = false; }
    }
    const errors = [
      ...(results.some((r) => r.status === "rejected") ? ["设备凭据清理未完成，请重试退出。"] : []),
      ...(!remoteConfirmed ? ["本机已退出；服务器撤销尚未确认，远程凭据将按有效期到期。"] : []),
    ];
    if (epoch === this.epoch) this.update({ phase: "login", error: errors.join(" ") || undefined });
  }

  async expired(reason = "登录已过期，请重新登录。"): Promise<void> {
    const epoch = this.next();
    this.windowOpen = false;
    this.landedUrl = null;
    try {
      await Promise.all([this.dependencies.tokens.clear(), this.cleanupSchoolData(epoch)]);
      if (epoch === this.epoch) this.update({ phase: "login", error: reason });
    } catch { await this.error(new Error("登录已过期，安全凭据清理失败，请重启后重新登录。"), epoch); }
  }
}
