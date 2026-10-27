import { LoaderCircle, LogIn, ShieldCheck } from "lucide-react";
import { Brand } from "./Brand";
import { familyCharacters } from "../appearance/AppearanceProvider";
import type { LoginSnapshot } from "../../../mobile/src/login/controller";

export function MobileLoginPage({ state, offline, onLogin, onAnonymous, onRetry }: {
  state: LoginSnapshot;
  offline: boolean;
  onLogin: () => void;
  onAnonymous: () => void;
  onRetry: () => void;
}) {
  const canReturn = state.phase === "ready";
  const reconnecting = !!state.session?.principal.authenticated;
  const busy = ["booting", "opening", "authorizing", "welcome"].includes(state.phase);
  return (
    <main className="login-page mobile-login-page">
      <div className="login-page__card">
        <Brand />
        <div className="login-family-art" aria-hidden="true">{familyCharacters.map((item) => <img key={item.id} src={item.src} alt="" />)}</div>
        <h1 className="login-page__title">{reconnecting ? "重新连接学校账号" : "把校园装进口袋"}</h1>
        <p className="login-page__lead">用学校账号连接课表与成绩。</p>
        {offline && <p className="login-page__error" role="status">当前离线，请连接网络后继续。</p>}
        {state.error && <p className="login-page__error" role="alert">{state.error}</p>}
        {state.schoolLoginError && <p className="login-page__error" role="alert">{state.schoolLoginError}</p>}
        {busy && <p className="mobile-login-progress" role="status"><LoaderCircle size={18} className="spin" />{state.phase === "welcome" ? `✅ 欢迎，${state.session?.jw_identity?.name || "同学"}` : state.phase === "booting" ? "正在检查安全会话…" : "请在学校登录页完成认证…"}</p>}
        <button type="button" className="login-page__primary" disabled={busy || offline || !!state.schoolLoginError} onClick={onLogin}><LogIn size={18} />用学校账号登录</button>
        <button type="button" className="login-page__ghost" disabled={busy || (!canReturn && offline)} onClick={onAnonymous}>{canReturn ? "返回小蛙助手" : "先匿名体验"}</button>
        {(state.error || state.schoolLoginError) && <button type="button" className="login-page__ghost" disabled={busy || offline} onClick={onRetry}>重新检查连接</button>}
        <p className="login-page__note"><ShieldCheck size={16} />密码与短信验证仅在学校页面完成。</p>
      </div>
    </main>
  );
}
