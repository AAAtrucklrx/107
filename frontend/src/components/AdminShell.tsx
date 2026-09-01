import {
  ArrowLeft,
  FlaskConical,
  LogOut,
  SearchCheck,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import type { ReactNode } from "react";
import type { PublicConfig, SessionPayload } from "../types";

export type AdminPage = "tools" | "knowledge";

const adminNavigation = [
  { page: "tools" as const, label: "工具审核", icon: Wrench },
  { page: "knowledge" as const, label: "知识审核", icon: SearchCheck },
];

export function AdminShell({
  children,
  config,
  session,
  page,
  onPageChange,
  onExit,
  onLogout,
  busy,
}: {
  children: ReactNode;
  config: PublicConfig;
  session: SessionPayload;
  page: AdminPage;
  onPageChange: (page: AdminPage) => void;
  onExit: () => void;
  onLogout: () => Promise<void>;
  busy: boolean;
}) {
  const profile = session.principal.profile;
  const environmentLabel = config.environment === "production"
    ? "生产环境"
    : config.environment === "competition" ? "比赛环境" : "开发环境";

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <div className="admin-brand"><ShieldCheck size={23} /><div><strong>小蜗</strong><span>管理后台</span></div></div>
        <div className="admin-environment" data-production={config.environment === "production" || undefined}>
          <FlaskConical size={14} />{environmentLabel}
        </div>
        <nav className="admin-navigation" aria-label="管理后台导航">
          {adminNavigation.map(({ page: itemPage, label, icon: Icon }) => (
            <button
              type="button"
              data-active={page === itemPage}
              aria-current={page === itemPage ? "page" : undefined}
              onClick={() => onPageChange(itemPage)}
              key={itemPage}
            >
              <Icon size={18} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="admin-sidebar__footer">
          <div className="admin-identity"><strong>{profile?.name || session.principal.id}</strong><span>{session.principal.id}</span></div>
          <button type="button" onClick={onExit}><ArrowLeft size={16} />返回用户端</button>
          <button type="button" disabled={busy} onClick={() => void onLogout()}><LogOut size={16} />退出登录</button>
        </div>
      </aside>

      <header className="admin-mobile-header">
        <div className="admin-brand"><ShieldCheck size={20} /><div><strong>小蜗</strong><span>管理后台</span></div></div>
        <button className="icon-button" type="button" aria-label="返回用户端" onClick={onExit}><ArrowLeft size={18} /></button>
      </header>

      <nav className="admin-mobile-navigation" aria-label="管理后台导航">
        {adminNavigation.map(({ page: itemPage, label, icon: Icon }) => (
          <button type="button" data-active={page === itemPage} onClick={() => onPageChange(itemPage)} key={itemPage}>
            <Icon size={17} />{label}
          </button>
        ))}
      </nav>

      <main className="admin-main">
        {session.principal.auth_mode === "demo" && (
          <div className="admin-demo-band" role="status">演示审核空间，与生产数据永久隔离</div>
        )}
        {children}
      </main>
    </div>
  );
}
