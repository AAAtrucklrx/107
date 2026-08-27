import { AnimatePresence, motion } from "motion/react";
import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "./components/AppShell";
import { apiMutation, bootstrap } from "./lib/api";
import { ChatWorkspace } from "./workspaces/ChatWorkspace";
import type { PublicConfig, SessionPayload, Theme, Workspace } from "./types";

const AcademicWorkspace = lazy(() => import("./workspaces/AcademicWorkspace").then((module) => ({ default: module.AcademicWorkspace })));
const CampusWorkspace = lazy(() => import("./workspaces/CampusWorkspace").then((module) => ({ default: module.CampusWorkspace })));
const ReviewWorkspace = lazy(() => import("./workspaces/ReviewWorkspace").then((module) => ({ default: module.ReviewWorkspace })));

function initialTheme(): Theme {
  const stored = localStorage.getItem("xiaowo-theme");
  return stored === "dark" ? "dark" : "light";
}

function workspaceFromPath(): Workspace {
  const value = window.location.pathname.replace(/^\/+|\/+$/g, "");
  return value === "academic" || value === "campus" || value === "review" ? value : "chat";
}

const workspacePaths: Record<Workspace, string> = {
  chat: "/",
  academic: "/academic",
  campus: "/campus",
  review: "/review",
};

export function App() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [workspace, setWorkspace] = useState<Workspace>(workspaceFromPath);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [seededQuestion, setSeededQuestion] = useState<string | undefined>();
  const [demoResetVersion, setDemoResetVersion] = useState(0);

  const navigateWorkspace = useCallback((next: Workspace, replace = false) => {
    setWorkspace(next);
    const path = workspacePaths[next];
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
  }, [theme]);

  useEffect(() => {
    const onPopState = () => setWorkspace(workspaceFromPath());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!session) return;
    if (workspace === "academic" && !session.capabilities.personal_academic) navigateWorkspace("chat", true);
    if (workspace === "review" && !session.capabilities.knowledge_review) navigateWorkspace("chat", true);
  }, [navigateWorkspace, session, workspace]);

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
    if (workspace === "campus") return <CampusWorkspace />;
    return <ReviewWorkspace session={session} />;
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
      busy={busy}
    >
      <AnimatePresence mode="wait">
        <motion.div
          key={workspace}
          className="workspace-transition"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
        >
          {error && <div className="inline-alert" role="alert">{error}</div>}
          <Suspense fallback={<div className="workspace-state" role="status"><LoaderCircle className="spin" size={20} /><span>正在载入工作区</span></div>}>
            {workspaceContent}
          </Suspense>
        </motion.div>
      </AnimatePresence>
    </AppShell>
  );
}
