import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { BookOpen, MessageCircle, Building2, ChevronLeft, ChevronDown, CircleUserRound, FlaskConical, LogIn, LogOut, Menu, Moon, RotateCcw, SearchCheck, Sun, UserRound, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Brand } from "./Brand";
import { AppScaffold, useViewportForm } from "../ui/AppScaffold";
import { TabBar, type NavigationItem } from "../ui/TabBar";
import { FamilyShortcut } from "../appearance/FamilyPicker";
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
  onOpenLogin: () => void;
  onLogout: () => Promise<void>;
  onDemoReset: () => Promise<void>;
  onOpenAdmin: () => void;
  busy: boolean;
  mobile?: boolean;
}

const items: NavigationItem[] = [
  { id: "chat", label: "问答", icon: MessageCircle },
  { id: "academic", label: "学业", icon: BookOpen },
  { id: "campus", label: "校园服务", icon: Building2 },
  { id: "profile", label: "我的", icon: UserRound },
];

export function AppShell(props: AppShellProps) {
  const { children, config, session, workspace, onWorkspaceChange, mobile = false } = props;
  const form = useViewportForm();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const identityName = session.principal.authenticated ? session.jw_identity?.name || session.principal.profile?.name || "同学" : "未登录";
  const select = (next: Workspace) => { setDrawerOpen(false); onWorkspaceChange(next); };
  const account = <AccountMenu {...props} showReview={session.capabilities.knowledge_review} />;
  const railLinks = (drawer = false) => <nav aria-label={drawer ? "展开的主导航" : "主导航"} className={drawer ? "scaffold-drawer-links" : "scaffold-rail-links"}>{items.map(({ id, label, icon: Icon }) =>
    <button key={id} type="button" aria-label={label} aria-current={workspace === id ? "page" : undefined} onClick={() => select(id)}><Icon size={21} strokeWidth={1.8} aria-hidden="true" /><span>{label}</span></button>
  )}</nav>;
  const rail = <><div className="scaffold-rail-brand"><Brand />{config.environment !== "production" && <span className="scaffold-environment"><FlaskConical size={13} />{config.environment === "competition" ? "比赛环境" : "开发环境"}</span>}</div>{railLinks()}<div className="scaffold-rail-account">{account}</div></>;
  const header = <>
    <div className="scaffold-header-leading">
      {form === "tablet" && <Dialog.Root open={drawerOpen} onOpenChange={setDrawerOpen}>
        <Dialog.Trigger asChild><button type="button" className="scaffold-icon-button" aria-label="展开导航栏"><Menu size={21} /></button></Dialog.Trigger>
        <Dialog.Portal><Dialog.Overlay className="scaffold-drawer-overlay" /><Dialog.Content className="scaffold-drawer" aria-describedby={undefined}>
          <div className="scaffold-drawer-head"><Dialog.Title>小蛙助手导航</Dialog.Title><Dialog.Close asChild><button type="button" className="scaffold-icon-button" aria-label="关闭导航栏"><X size={20} /></button></Dialog.Close></div>
          {railLinks(true)}<p className="scaffold-drawer-note">问校园，查学业，安排每一天。</p>
        </Dialog.Content></Dialog.Portal>
      </Dialog.Root>}
      {form === "phone" ? <>{workspace === "campus" && <button type="button" className="scaffold-icon-button" aria-label="返回我的" onClick={() => select("profile")}><ChevronLeft size={20} /></button>}<span className="scaffold-product-name">小蛙助手</span></> : <span className="scaffold-section-name">{items.find((item) => item.id === workspace)?.label}</span>}
    </div>
    <div className="scaffold-header-actions">
      <FamilyShortcut />
      {form !== "phone" && <button className="scaffold-profile-shortcut" type="button" onClick={() => select("profile")} aria-label={`我的，${identityName}`}>
        <span className="scaffold-avatar" aria-hidden="true">{session.principal.authenticated ? identityName.slice(0, 1) : <UserRound size={19} />}</span>
        <span className="scaffold-profile-label">{identityName}</span>
      </button>}
    </div>
  </>;
  return <Tooltip.Provider delayDuration={500}><AppScaffold form={form} header={header} native={mobile} navigation={form === "phone" ? <TabBar items={items.filter((item) => item.id !== "campus")} current={workspace === "campus" ? "profile" : workspace} onSelect={select} /> : rail}>
    {session.principal.auth_mode === "demo" && session.principal.authenticated && <div className="scaffold-demo-band" role="status"><span>演示数据</span><span>{session.principal.profile?.major} · {session.principal.profile?.grade}</span></div>}
    <main className="workspace-canvas" data-workspace={workspace}>{children}</main>
  </AppScaffold></Tooltip.Provider>;
}

interface AccountMenuProps {
  config: PublicConfig;
  session: SessionPayload;
  theme: Theme;
  onThemeToggle: () => void;
  onDemoLogin: () => Promise<void>;
  onOpenLogin: () => void;   // 2026-09-29：打开独立正式登录页
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
  onOpenLogin,
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
            {session.jw_identity?.name?.slice(0, 1) || (authenticated ? profile?.name?.slice(0, 1) : <CircleUserRound size={18} />)}
          </span>
          {!compact && (
            <span className="account-trigger__text">
              <strong>{session.jw_identity?.name || (authenticated ? profile?.name : "未登录")}</strong>
              {session.jw_identity?.student_id && <em>{session.jw_identity.student_id}</em>}
              <small>{authenticated && session.jw_session ? "已连接教务" : authenticated ? profile?.id : "公共能力"}</small>
            </span>
          )}
          <ChevronDown size={15} aria-hidden="true" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="account-menu" sideOffset={8} align="end">
          {authenticated && profile && (
            <div className="account-menu__identity">
              <strong>{session.jw_identity?.name || profile.name}</strong>
              <span>{session.jw_identity?.major || profile.major} · {session.jw_identity?.grade || profile.grade}</span>
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
          {authenticated && session.principal.auth_mode === "demo" && config.features.demo_reset_enabled === true && (
            <DropdownMenu.Item
              className="account-menu__item"
              disabled={busy}
              onSelect={() => {
                if (window.prompt("恢复演示初始状态将清空演示数据。请输入「重置」以确认：") === "重置") {
                  void onDemoReset();
                }
              }}
            >
              <RotateCcw size={17} />
              恢复演示初始状态
            </DropdownMenu.Item>
          )}
          {!authenticated && (
            <DropdownMenu.Item
              className="account-menu__item account-menu__item--primary"
              disabled={busy}
              onSelect={onOpenLogin}
            >
              <LogIn size={17} />
              登录（统一认证 / 演示身份）
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
