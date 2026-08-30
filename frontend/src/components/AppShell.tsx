import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  BookOpen,
  Bot,
  Building2,
  ChevronDown,
  CircleUserRound,
  FlaskConical,
  LogIn,
  LogOut,
  Moon,
  SearchCheck,
  Sun,
  RotateCcw,
} from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
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
  busy: boolean;
}

const workspaceMeta: Record<Workspace, { label: string; icon: typeof Bot }> = {
  chat: { label: "问小蜗", icon: Bot },
  academic: { label: "我的学业", icon: BookOpen },
  campus: { label: "校园服务", icon: Building2 },
  review: { label: "知识审核", icon: SearchCheck },
};

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
  busy,
}: AppShellProps) {
  const authenticated = session.principal.authenticated;
  const showAcademic = session.capabilities.personal_academic;
  const showReview = session.capabilities.knowledge_review;
  const profile = session.principal.profile;

  return (
    <Tooltip.Provider delayDuration={500}>
      <div className="app-shell">
        <aside className="desktop-rail">
          <Brand />
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
              onOpenReview={() => onWorkspaceChange("review")}
              showReview={showReview}
              busy={busy}
            />
          </div>
        </aside>

        <header className="mobile-header">
          <Brand compact />
          <AccountMenu
            config={config}
            session={session}
            theme={theme}
            onThemeToggle={onThemeToggle}
            onDemoLogin={onDemoLogin}
            onLogout={onLogout}
            onDemoReset={onDemoReset}
            onOpenReview={() => onWorkspaceChange("review")}
            showReview={showReview}
            busy={busy}
            compact
          />
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
  onOpenReview: () => void;
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
  onOpenReview,
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
            <DropdownMenu.Item className="account-menu__item" onSelect={onOpenReview}>
              <SearchCheck size={17} />
              知识审核
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
