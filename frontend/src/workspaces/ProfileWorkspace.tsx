import { FamilyPicker } from "../appearance/FamilyPicker";
import { familyCharacters, useAppearance } from "../appearance/AppearanceProvider";
import { BookOpen, ChevronRight, CircleUserRound, GraduationCap, Info, LogIn, LogOut, Moon, RotateCcw, SearchCheck, ShieldCheck, Sun } from "lucide-react";
import { useSession } from "../session/SessionProvider";
import type { Workspace } from "../types";
import { StylePicker } from "../appearance/StylePicker";

export function ProfileWorkspace({ onNavigate, onLogout, onDemoReset, onOpenAdmin }: {
  onNavigate: (workspace: Workspace) => void;
  onLogout: () => Promise<void>;
  onDemoReset: () => Promise<void>;
  onOpenAdmin: () => void;
}) {
  const { config, session, busy, theme, toggleTheme, openLogin, offline, mobile } = useSession();
  const { character, style } = useAppearance();
  const companion = familyCharacters.find((item) => item.id === character)!;
  if (!session || !config) return null;
  const authenticated = session.principal.authenticated;
  const identity = session.jw_identity;
  const profile = authenticated ? session.principal.profile : null;
  const name = authenticated ? identity?.name || profile?.name || "同学" : "未登录";
  const studentId = authenticated ? identity?.student_id || profile?.id : undefined;
  const connected = authenticated && !!session.jw_session;
  const connectionAction = connected ? (mobile ? "重新连接教务" : "管理教务连接") : "连接学校账号";
  const demo = session.principal.auth_mode === "demo" && authenticated;
  return <div className="profile-workspace">
    <header className="profile-page-heading"><h1>我的</h1></header>
    <section className="profile-identity" aria-label="个人身份">
      <span className="profile-avatar" aria-hidden="true">{style === "frog" ? <img src={companion.src} alt="" /> : authenticated ? name.slice(0, 1) : <CircleUserRound size={26} />}</span>
      <div className="profile-identity__details"><h2>{name}</h2>{studentId && <p>{studentId}</p>}
        {authenticated && identity?.college && <p>{identity.college}</p>}
        {authenticated && <p>{[identity?.major || profile?.major, identity?.grade || profile?.grade].filter(Boolean).join(" · ")}</p>}
        {!authenticated && <p>连接你的课表与成绩。</p>}
        <span className="profile-connection" data-connected={connected}><ShieldCheck size={15} />{connected ? "已连接教务" : demo ? "演示身份 · 样例数据" : "教务未连接"}</span>
      </div>
      {!authenticated && <button className="profile-primary" type="button" onClick={openLogin}><LogIn size={17} />登录</button>}
    </section>
    {offline && <p className="profile-offline" role="status">当前离线，联网后即可继续查询。</p>}
    <section className="profile-settings" aria-labelledby="profile-services"><h2 id="profile-services">校园与服务</h2>
      <button type="button" onClick={() => onNavigate("academic")}><GraduationCap size={20} /><span>我的学业</span><ChevronRight size={18} /></button>
      <button type="button" onClick={() => onNavigate("campus")} aria-label="浏览校园服务"><BookOpen size={20} /><span>校园服务</span><ChevronRight size={18} /></button>
      {session.capabilities.knowledge_review && <button type="button" onClick={onOpenAdmin}><SearchCheck size={20} /><span>管理后台</span><ChevronRight size={18} /></button>}
    </section>
    <FamilyPicker />
    <StylePicker />
    <section className="profile-settings" aria-labelledby="profile-preferences"><h2 id="profile-preferences">偏好与账号</h2>
      <button type="button" onClick={toggleTheme}>{theme === "light" ? <Moon size={20} /> : <Sun size={20} />}<span>深浅模式</span><span className="profile-setting-value">切换{theme === "light" ? "深色" : "浅色"}</span></button>
      {authenticated && <button type="button" disabled={busy} onClick={openLogin}><ShieldCheck size={20} /><span>{connectionAction}</span><ChevronRight size={18} /></button>}
      {demo && config.features.demo_reset_enabled && <button type="button" disabled={busy} onClick={() => {
        if (window.prompt("恢复演示初始状态将清空演示数据。请输入「重置」以确认：") === "重置") void onDemoReset();
      }}><RotateCcw size={20} /><span>恢复演示初始状态<small>仅影响演示数据</small></span><ChevronRight size={18} /></button>}
      {authenticated && <button type="button" className="profile-logout" disabled={busy} onClick={() => void onLogout()}><LogOut size={20} /><span>{busy ? "正在退出…" : "退出登录"}</span><ChevronRight size={18} /></button>}
    </section>
    <details className="profile-about"><summary><Info size={17} aria-hidden="true" />关于小蛙助手</summary><p>中国科学技术大学校园 AI 助手。</p><small>版本 {config.version} · 个人信息仅用于你的校园查询</small></details>
  </div>;
}
