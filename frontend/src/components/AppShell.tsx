import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  BookOpen,
  Bot,
  Building2,
  ChevronDown,
  CircleUserRound,
  FlaskConical,
  History,
  LogIn,
  LogOut,
  Moon,
  SearchCheck,
  Sun,
  RotateCcw,
} from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useState } from "react";
import { Brand } from "./Brand";
import type { PublicConfig, SessionPayload, Theme, Workspace } from "../types";

interface AppShellProps {
  children: ReactNode;
  config: PublicConfig;
  session: SessionPayload;
  workspace: Workspace;
  onWorkspaceChange: (workspace: Workspace) => void;
  theme: Theme;
  onThemeToggle: () => void;
  onDemoLogin: () => Promise<void>;
  onLogout: () => Promise<void>;
  onDemoReset: () => Promise<void>;
  onOpenAdmin: () => void;
  busy: boolean;
}

const workspaceMeta: Record<Workspace, { label: string; icon: typeof Bot }> = {
  chat: { label: "问小蜗", icon: Bot },
  academic: { label: "我的学业", icon: BookOpen },
  campus: { label: "校园服务", icon: Building2 },
};

function openHistoryDrawer() {
  window.dispatchEvent(new CustomEvent("xiaowo:open-history"));
}

function topbarGreeting(profile: { name?: string | null } | null | undefined, authenticated: boolean) {
  const hour = new Date().getHours();
  const greet = hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const name = authenticated && profile?.name ? profile.name : "游客";
  const date = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date());
  return `${greet}，${name} · ${date}`;
}

function NavButton({
  workspace,
  current,
  onSelect,
}: {
  workspace: Workspace;
  current: Workspace;
  onSelect: (value: Workspace) => void;
}) {
  const meta = workspaceMeta[workspace];
  const Icon = meta.icon;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          type="button"
          className="workspace-nav__item"
          data-active={current === workspace}
          aria-current={current === workspace ? "page" : undefined}
          aria-label={meta.label}
          onClick={() => onSelect(workspace)}
        >
          <Icon size={19} strokeWidth={1.8} aria-hidden="true" />
          <span>{meta.label}</span>
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="tooltip" side="right" sideOffset={10}>
          {meta.label}
          <Tooltip.Arrow className="tooltip__arrow" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function AppShell({
  children,
  config,
  session,
  workspace,
  onWorkspaceChange,
  theme,
  onThemeToggle,
  onDemoLogin,
  onLogout,
  onDemoReset,
  onOpenAdmin,
  busy,
}: AppShellProps) {
  const authenticated = session.principal.authenticated;
  const showAcademic = session.capabilities.personal_academic;
  const showReview = session.capabilities.knowledge_review;
  const profile = session.principal.profile;

  // 侧栏展开/收起：Web 默认展开，移动端默认收起；记住上次选择。
  // 收起态点品牌色块 → 抽屉浮层；常驻态点品牌色块 → 收起。
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem("xiaowo-rail");
      if (saved === "collapsed") return true;
      if (saved === "expanded") return false;
      return window.matchMedia?.("(max-width: 760px)")?.matches ?? false;
    } catch {
      return false;
    }
  });
  const [railDrawerOpen, setRailDrawerOpen] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem("xiaowo-rail", railCollapsed ? "collapsed" : "expanded");
    } catch {
      /* 隐私模式下静默 */
    }
  }, [railCollapsed]);

  useEffect(() => {
    if (!railDrawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setRailDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [railDrawerOpen]);

  return (
    <Tooltip.Provider delayDuration={500}>
      <div className={`app-shell${railCollapsed ? " app-shell--rail-collapsed" : ""}`}>
        {railCollapsed && (
          <header className="rail-topbar">
            <button
              type="button"
              className="rail-brand-toggle"
              onClick={() => setRailDrawerOpen(true)}
              aria-label="展开导航栏"
              aria-expanded={railDrawerOpen}
            >
              <Brand />
            </button>
            <div className="rail-topbar__greeting" role="status">
              {topbarGreeting(profile, authenticated)}
            </div>
            <button
              type="button"
              className="icon-button rail-topbar__theme"
              onClick={openHistoryDrawer}
              aria-label="会话历史"
            >
              <History size={17} />
            </button>
            <button
              type="button"
              className="icon-button rail-topbar__theme"
              onClick={onThemeToggle}
              aria-label={theme === "light" ? "深色主题" : "浅色主题"}
            >
              {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            </button>
          </header>
        )}
        {railCollapsed && railDrawerOpen && (
          <div className="rail-scrim" onClick={() => setRailDrawerOpen(false)} aria-hidden="true" />
        )}
        <aside
          className={`desktop-rail${railCollapsed ? " desktop-rail--drawer" : ""}`}
          data-open={railCollapsed ? railDrawerOpen : true}
          aria-hidden={railCollapsed && !railDrawerOpen ? true : undefined}
        >
          <button
            type="button"
            className="rail-brand-toggle"
            onClick={() => {
              if (!railCollapsed) {
                setRailCollapsed(true);
              } else if (railDrawerOpen) {
                // 抽屉态点品牌：固定侧栏，回到常驻展开
                setRailCollapsed(false);
                setRailDrawerOpen(false);
              } else {
                setRailDrawerOpen(false);
              }
            }}
            aria-label={!railCollapsed ? "收起导航栏" : railDrawerOpen ? "固定导航栏" : "关闭导航栏"}
            aria-expanded={railCollapsed ? railDrawerOpen : true}
          >
            <Brand />
          </button>
          {config.environment !== "production" && (
            <div className="environment-stamp" aria-label={config.environment === "competition" ? "比赛环境" : "开发环境"}>
              <FlaskConical size={14} aria-hidden="true" />
              <span>{config.environment === "competition" ? "比赛环境" : "开发环境"}</span>
            </div>
          )}
          <nav className="workspace-nav" aria-label="工作区">
            <NavButton workspace="chat" current={workspace} onSelect={onWorkspaceChange} />
            {showAcademic && (
              <NavButton workspace="academic" current={workspace} onSelect={onWorkspaceChange} />
            )}
            <NavButton workspace="campus" current={workspace} onSelect={onWorkspaceChange} />
          </nav>
          <div className="desktop-rail__footer">
            <AccountMenu
              config={config}
              session={session}
              theme={theme}
              onThemeToggle={onThemeToggle}
              onDemoLogin={onDemoLogin}
              onLogout={onLogout}
              onDemoReset={onDemoReset}
              onOpenAdmin={onOpenAdmin}
              showReview={showReview}
              busy={busy}
            />
          </div>
        </aside>

        <header className="mobile-header">
          <Brand compact />
          <div className="mobile-header__actions">
            <button
              type="button"
              className="icon-button"
              onClick={openHistoryDrawer}
              aria-label="会话历史"
            >
              <History size={18} />
            </button>
            <AccountMenu
            config={config}
            session={session}
            theme={theme}
            onThemeToggle={onThemeToggle}
            onDemoLogin={onDemoLogin}
            onLogout={onLogout}
            onDemoReset={onDemoReset}
            onOpenAdmin={onOpenAdmin}
            showReview={showReview}
            busy={busy}
            compact
          />
          </div>
        </header>

        <div className="workbench">
          {session.principal.auth_mode === "demo" && authenticated && (
            <div className="demo-band" role="status">
              <span>演示数据</span>
              <span>{profile?.id} · {profile?.major} · {profile?.grade}</span>
            </div>
          )}
          <main className="workspace-canvas" data-workspace={workspace}>
            {children}
          </main>
        </div>

        <nav className="mobile-bottom-nav" aria-label="工作区" style={{ "--mobile-nav-count": showAcademic ? 3 : 2 } as CSSProperties}>
          <NavButton workspace="chat" current={workspace} onSelect={onWorkspaceChange} />
          {showAcademic && (
            <NavButton workspace="academic" current={workspace} onSelect={onWorkspaceChange} />
          )}
          <NavButton workspace="campus" current={workspace} onSelect={onWorkspaceChange} />
        </nav>
      </div>
    </Tooltip.Provider>
  );
}

interface AccountMenuProps {
  config: PublicConfig;
  session: SessionPayload;
  theme: Theme;
  onThemeToggle: () => void;
  onDemoLogin: () => Promise<void>;
  onLogout: () => Promise<void>;
  onDemoReset: () => Promise<void>;
  onOpenAdmin: () => void;
  showReview: boolean;
  busy: boolean;
  compact?: boolean;
}

function AccountMenu({
  config,
  session,
  theme,
  onThemeToggle,
  onDemoLogin,
  onLogout,
  onDemoReset,
  onOpenAdmin,
  showReview,
  busy,
  compact = false,
}: AccountMenuProps) {
  const profile = session.principal.profile;
  const authenticated = session.principal.authenticated;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className={`account-trigger ${compact ? "account-trigger--compact" : ""}`}>
          <span className="account-trigger__avatar" aria-hidden="true">
            {authenticated ? profile?.name?.slice(0, 1) : <CircleUserRound size={18} />}
          </span>
          {!compact && (
            <span className="account-trigger__text">
              <strong>{authenticated ? profile?.name : "未登录"}</strong>
              <small>{authenticated ? profile?.id : "公共能力"}</small>
            </span>
          )}
          <ChevronDown size={15} aria-hidden="true" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="account-menu" sideOffset={8} align="end">
          {authenticated && profile && (
            <div className="account-menu__identity">
              <strong>{profile.name}</strong>
              <span>{profile.major} · {profile.grade}</span>
              {session.principal.auth_mode === "demo" && <em>演示数据</em>}
            </div>
          )}
          {showReview && (
            <DropdownMenu.Item className="account-menu__item" onSelect={onOpenAdmin}>
              <SearchCheck size={17} />
              管理后台
            </DropdownMenu.Item>
          )}
          <DropdownMenu.Item className="account-menu__item" onSelect={onThemeToggle}>
            {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            {theme === "light" ? "深色主题" : "浅色主题"}
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="account-menu__separator" />
          {authenticated && session.principal.auth_mode === "demo" && (
            <DropdownMenu.Item
              className="account-menu__item"
              disabled={busy}
              onSelect={() => void onDemoReset()}
            >
              <RotateCcw size={17} />
              恢复演示初始状态
            </DropdownMenu.Item>
          )}
          {!authenticated && config.auth_mode === "demo" && (
            <DropdownMenu.Item
              className="account-menu__item account-menu__item--primary"
              disabled={busy}
              onSelect={() => void onDemoLogin()}
            >
              <LogIn size={17} />
              进入演示身份
            </DropdownMenu.Item>
          )}
          {!authenticated && config.auth_mode === "cas" && (
            <DropdownMenu.Item className="account-menu__item account-menu__item--primary" asChild>
              <a href="/api/v1/auth/cas/login">
                <LogIn size={17} />
                科大统一认证
              </a>
            </DropdownMenu.Item>
          )}
          {authenticated && (
            <DropdownMenu.Item
              className="account-menu__item account-menu__item--danger"
              disabled={busy}
              onSelect={() => void onLogout()}
            >
              <LogOut size={17} />
              退出登录
            </DropdownMenu.Item>
          )}
          {!authenticated && config.auth_mode === "anonymous" && (
            <div className="account-menu__anonymous">匿名会话仅保存在当前浏览器</div>
          )}
          <DropdownMenu.Arrow className="account-menu__arrow" />
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
