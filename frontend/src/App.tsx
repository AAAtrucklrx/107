import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "./components/AdminShell";
import type { AdminPage } from "./components/AdminShell";
import { AppShell } from "./components/AppShell";
import { apiMutation, bootstrap } from "./lib/api";
import { ChatWorkspace } from "./workspaces/ChatWorkspace";
import type { PublicConfig, SessionPayload, Theme, Workspace } from "./types";

const AcademicWorkspace = lazy(() => import("./workspaces/AcademicWorkspace").then((module) => ({ default: module.AcademicWorkspace })));
const CampusWorkspace = lazy(() => import("./workspaces/CampusWorkspace").then((module) => ({ default: module.CampusWorkspace })));
const AdminToolsWorkspace = lazy(() => import("./workspaces/AdminToolsWorkspace").then((module) => ({ default: module.AdminToolsWorkspace })));
const ReviewWorkspace = lazy(() => import("./workspaces/ReviewWorkspace").then((module) => ({ default: module.ReviewWorkspace })));

function initialTheme(): Theme {
  const stored = localStorage.getItem("xiaowo-theme");
  return stored === "dark" ? "dark" : "light";
}

function workspaceFromPath(): Workspace {
  const value = window.location.pathname.replace(/^\/+|\/+$/g, "");
  return value === "academic" || value === "campus" ? value : "chat";
}

function adminPageFromPath(): AdminPage | null {
  const value = window.location.pathname.replace(/^\/+|\/+$/g, "");
  if (value === "review") {
    window.history.replaceState({}, "", "/admin/knowledge");
    return "knowledge";
  }
  if (value === "admin" || value === "admin/tools") return "tools";
  if (value === "admin/knowledge") return "knowledge";
  return null;
}

const workspacePaths: Record<Workspace, string> = {
  chat: "/",
  academic: "/academic",
  campus: "/campus",
};

const adminPaths: Record<AdminPage, string> = {
  tools: "/admin",
  knowledge: "/admin/knowledge",
};

export function App() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [workspace, setWorkspace] = useState<Workspace>(workspaceFromPath);
  const [adminPage, setAdminPage] = useState<AdminPage | null>(adminPageFromPath);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [seededQuestion, setSeededQuestion] = useState<string | undefined>();
  const [demoResetVersion, setDemoResetVersion] = useState(0);

  const navigateWorkspace = useCallback((next: Workspace, replace = false) => {
    setAdminPage(null);
    setWorkspace(next);
    const path = workspacePaths[next];
    if (window.location.pathname !== path) {
      window.history[replace ? "replaceState" : "pushState"]({}, "", path);
    }
  }, []);

  const navigateAdmin = useCallback((next: AdminPage, replace = false) => {
    setAdminPage(next);
    const path = adminPaths[next];
    if (window.location.pathname !== path) {
      window.history[replace ? "replaceState" : "pushState"]({}, "", path);
    }
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const result = await bootstrap();
      setConfig(result.config);
      setSession(result.session);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法连接小蜗服务。" );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("xiaowo-theme", theme);
    document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute(
      "content",
      theme === "dark" ? "#0D141A" : "#F4F7FA",
    );
  }, [theme]);

  useEffect(() => {
    const onPopState = () => {
      setWorkspace(workspaceFromPath());
      setAdminPage(adminPageFromPath());
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!session) return;
    if (workspace === "academic" && !session.capabilities.personal_academic) navigateWorkspace("chat", true);
    if (adminPage && !session.capabilities.knowledge_review) navigateWorkspace("chat", true);
  }, [adminPage, navigateWorkspace, session, workspace]);

  const handleDemoLogin = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const next = await apiMutation<SessionPayload>("/auth/demo", session.csrf_token, {
        method: "POST",
        body: "{}",
      });
      setSession(next);
      navigateWorkspace("academic");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "演示登录失败。" );
    } finally {
      setBusy(false);
    }
  }, [navigateWorkspace, session]);

  const handleLogout = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    try {
      await apiMutation("/auth/logout", session.csrf_token, { method: "POST", body: "{}" });
      await load();
      navigateWorkspace("chat");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退出登录失败。" );
    } finally {
      setBusy(false);
    }
  }, [load, navigateWorkspace, session]);

  const handleDemoReset = useCallback(async () => {
    if (!session || session.principal.auth_mode !== "demo") return;
    setBusy(true);
    setError(null);
    try {
      await apiMutation("/auth/demo/reset", session.csrf_token, { method: "POST", body: "{}" });
      setDemoResetVersion((value) => value + 1);
      navigateWorkspace("chat");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "恢复演示数据失败。" );
    } finally {
      setBusy(false);
    }
  }, [navigateWorkspace, session]);

  const workspaceContent = useMemo(() => {
    if (!config || !session) return null;
    if (workspace === "chat") {
      return <ChatWorkspace key={`chat-${demoResetVersion}`} config={config} session={session} seededQuestion={seededQuestion} onSeedConsumed={() => setSeededQuestion(undefined)} />;
    }
    if (workspace === "academic") {
      return <AcademicWorkspace session={session} onAsk={(question) => { setSeededQuestion(question); navigateWorkspace("chat"); }} />;
    }
    if (workspace === "campus") return <CampusWorkspace session={session} />;
    return <CampusWorkspace session={session} />;
  }, [config, demoResetVersion, navigateWorkspace, seededQuestion, session, workspace]);

  if (!config || !session) {
    return (
      <div className="boot-screen">
        {error ? (
          <div className="boot-error" role="alert">
            <AlertCircle size={24} />
            <p>{error}</p>
            <button type="button" className="command-button" onClick={() => void load()}>
              <RefreshCw size={17} />
              重试
            </button>
          </div>
        ) : (
          <div className="boot-loader" role="status">
            <LoaderCircle size={28} className="spin" />
            <span>正在连接小蜗</span>
          </div>
        )}
      </div>
    );
  }

  if (adminPage && session.capabilities.knowledge_review) {
    return (
      <AdminShell
        config={config}
        session={session}
        page={adminPage}
        onPageChange={navigateAdmin}
        onExit={() => navigateWorkspace("chat")}
        onLogout={handleLogout}
        busy={busy}
      >
        <Suspense fallback={<div className="workspace-state" role="status"><LoaderCircle className="spin" size={20} /><span>正在载入管理后台</span></div>}>
          {adminPage === "tools" ? <AdminToolsWorkspace session={session} /> : <ReviewWorkspace session={session} />}
        </Suspense>
      </AdminShell>
    );
  }

  return (
    <AppShell
      config={config}
      session={session}
      workspace={workspace}
      onWorkspaceChange={navigateWorkspace}
      theme={theme}
      onThemeToggle={() => setTheme((value) => value === "light" ? "dark" : "light")}
      onDemoLogin={handleDemoLogin}
      onLogout={handleLogout}
      onDemoReset={handleDemoReset}
      onOpenAdmin={() => navigateAdmin("tools")}
      busy={busy}
    >
      <div className="workspace-transition">
        {error && <div className="inline-alert" role="alert">{error}</div>}
        <Suspense fallback={<div className="workspace-state" role="status"><LoaderCircle className="spin" size={20} /><span>正在载入工作区</span></div>}>
          {workspaceContent}
        </Suspense>
      </div>
    </AppShell>
  );
}
