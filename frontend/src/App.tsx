import { AlertCircle, BookOpen, LoaderCircle, RefreshCw } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { AdminShell } from "./components/AdminShell";
import type { AdminPage } from "./components/AdminShell";
import { LoginPage } from "./components/LoginPage";
import { MobileLoginPage } from "./components/MobileLoginPage";
import { AppShell } from "./components/AppShell";
import { SessionProvider, useSession } from "./session/SessionProvider";
import { ChatWorkspace } from "./workspaces/ChatWorkspace";
import { ProfileWorkspace } from "./workspaces/ProfileWorkspace";
import { AppearanceProvider } from "./appearance/AppearanceProvider";
import type { Workspace } from "./types";

const AcademicWorkspace = lazy(() => import("./workspaces/AcademicWorkspace").then((module) => ({ default: module.AcademicWorkspace })));
const CampusWorkspace = lazy(() => import("./workspaces/CampusWorkspace").then((module) => ({ default: module.CampusWorkspace })));
const AdminToolsWorkspace = lazy(() => import("./workspaces/AdminToolsWorkspace").then((module) => ({ default: module.AdminToolsWorkspace })));
const ReviewWorkspace = lazy(() => import("./workspaces/ReviewWorkspace").then((module) => ({ default: module.ReviewWorkspace })));

function workspaceFromPath(): Workspace {
  const value = window.location.pathname.replace(/^\/+|\/+$/g, "");
  return value === "academic" || value === "campus" || value === "profile" ? value : "chat";
}

function adminPageFromPath(): AdminPage | null {
  const value = window.location.pathname.replace(/^\/+|\/+$/g, "");
  if (value === "review") {
    window.history.replaceState({}, "", "/admin/knowledge");
    return "knowledge";
  }
  // 2026-09-17 修 P2-5：裸 /admin 默认进「知识审核」（管理主战场）。
  // 原来默认落「工具审核」，而那是常空页，第一次进后台的人会以为后台没东西。
  if (value === "admin") {
    window.history.replaceState({}, "", "/admin/knowledge");
    return "knowledge";
  }
  if (value === "admin/tools") return "tools";
  if (value === "admin/knowledge") return "knowledge";
  return null;
}

const workspacePaths: Record<Workspace, string> = {
  chat: "/",
  academic: "/academic",
  campus: "/campus",
  profile: "/profile",
};

const adminPaths: Record<AdminPage, string> = {
  tools: "/admin",
  knowledge: "/admin/knowledge",
};

function navigateMobilePath(path: string) {
  window.history.replaceState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function App() {
  return <AppearanceProvider><SessionProvider onNavigate={navigateMobilePath}><Application /></SessionProvider></AppearanceProvider>;
}

function Application() {
  const { config, session, mobile, mobileState, offline, error, busy, theme, toggleTheme,
    loginOpen, openLogin, closeLogin, reload, demoLogin, logout, demoReset, demoResetVersion,
    startSchoolLogin, enterAnonymous, retryMobile } = useSession();
  const [workspace, setWorkspace] = useState<Workspace>(workspaceFromPath);
  const [adminPage, setAdminPage] = useState<AdminPage | null>(adminPageFromPath);
  const [seededQuestion, setSeededQuestion] = useState<string | undefined>();

  const navigateWorkspace = useCallback((next: Workspace, replace = false) => {
    setAdminPage(null);
    setWorkspace(next);
    const path = workspacePaths[next];
    if (window.location.pathname !== path) window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  }, []);
  const navigateAdmin = useCallback((next: AdminPage, replace = false) => {
    setAdminPage(next);
    const path = adminPaths[next];
    if (window.location.pathname !== path) window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  }, []);

  useEffect(() => {
    const onPopState = () => { setWorkspace(workspaceFromPath()); setAdminPage(adminPageFromPath()); };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  useEffect(() => {
    if (session && adminPage && !session.capabilities.knowledge_review) navigateWorkspace("chat", true);
  }, [adminPage, navigateWorkspace, session]);

  const handleDemoLogin = async () => { if (await demoLogin()) navigateWorkspace("academic"); };
  const handleLogout = async () => { if (await logout()) navigateWorkspace("chat"); };
  const handleDemoReset = async () => { if (await demoReset()) navigateWorkspace("chat"); };
  const ask = (question: string) => { setSeededQuestion(question); navigateWorkspace("chat"); };

  if (mobile && mobileState && (mobileState.phase !== "ready" || loginOpen)) {
    return <MobileLoginPage state={mobileState} offline={offline} onLogin={startSchoolLogin} onAnonymous={enterAnonymous} onRetry={retryMobile} />;
  }
  if (!config || !session) {
    return <div className="boot-screen">{error ? <div className="boot-error" role="alert">
      <AlertCircle size={24} /><p>{error}</p><button type="button" className="command-button" onClick={() => void reload()}><RefreshCw size={17} />重试</button>
    </div> : <div className="boot-loader" role="status"><LoaderCircle size={28} className="spin" /><span>正在连接小蛙助手</span></div>}</div>;
  }
  if (loginOpen) return <LoginPage config={config} busy={busy} error={error} onDemoLogin={() => void handleDemoLogin()} onClose={closeLogin} />;
  if (adminPage && session.capabilities.knowledge_review) {
    return <AdminShell config={config} session={session} page={adminPage} onPageChange={navigateAdmin} onExit={() => navigateWorkspace("chat")} onLogout={handleLogout} busy={busy}>
      <Suspense fallback={<WorkspaceLoading label="正在载入管理后台" />}>{adminPage === "tools" ? <AdminToolsWorkspace session={session} /> : <ReviewWorkspace session={session} />}</Suspense>
    </AdminShell>;
  }

  let content;
  if (workspace === "academic") content = session.capabilities.personal_academic
    ? <AcademicWorkspace session={session} onAsk={ask} />
    : <section className="academic-login-gate"><span className="academic-login-gate__icon"><BookOpen size={30} /></span><p className="page-eyebrow">我的学业</p><h1>连接你的学业生活</h1><p>登录后查看自己的课表、成绩与培养方案，所有个人信息都来自你的教务账号。</p><button className="profile-primary" type="button" onClick={openLogin}>登录后查看学业</button><button className="profile-text-button" type="button" onClick={() => navigateWorkspace("chat")}>先问一个校园问题</button></section>;
  else if (workspace === "profile") content = <ProfileWorkspace onNavigate={navigateWorkspace} onLogout={handleLogout} onDemoReset={handleDemoReset} onOpenAdmin={() => navigateAdmin("knowledge")} />;
  else if (workspace === "campus") content = <CampusWorkspace session={session} />;

  return <AppShell config={config} session={session} workspace={workspace} onWorkspaceChange={navigateWorkspace} theme={theme} onThemeToggle={toggleTheme} onDemoLogin={handleDemoLogin} onOpenLogin={openLogin} onLogout={handleLogout} onDemoReset={handleDemoReset} onOpenAdmin={() => navigateAdmin("knowledge")} busy={busy} mobile={mobile}>
    <div className="workspace-transition">
      {offline && <div className="inline-alert" role="status">当前离线，发送消息和查询教务需要网络连接。</div>}
      {error && <div className="inline-alert" role="alert">{error}</div>}
      {mobileState?.schoolLoginError && <div className="inline-alert" role="alert">{mobileState.schoolLoginError}</div>}
      <div className="persistent-chat" hidden={workspace !== "chat"}>
        <ChatWorkspace active={workspace === "chat"} key={`chat-${demoResetVersion}-${session.principal.auth_mode}-${session.principal.id ?? "anonymous"}-${session.capabilities.server_history}`} config={config} session={session} theme={theme} onThemeToggle={toggleTheme} seededQuestion={seededQuestion} onSeedConsumed={() => setSeededQuestion(undefined)} />
      </div>
      <Suspense fallback={<WorkspaceLoading label="正在载入工作区" />}>{content}</Suspense>
    </div>
  </AppShell>;
}

function WorkspaceLoading({ label }: { label: string }) {
  return <div className="workspace-state" role="status"><LoaderCircle className="spin" size={20} /><span>{label}</span></div>;
}
