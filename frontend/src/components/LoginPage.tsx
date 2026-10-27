import { useState } from "react";
import { Info, LogIn, ShieldCheck, X } from "lucide-react";
import { Brand } from "./Brand";
import { SsoImportPanel } from "./SsoImportPanel";
import { useOptionalSession } from "../session/SessionProvider";
import type { PublicConfig } from "../types";

/**
 * 小蛙助手「正式登录页」（2026-09-29）。
 *
 * 背景：此前登录只藏在左下角头像菜单里的两个条目（「进入演示身份」/「科大统一认证」），
 * 给学校老师演示时既不好找也不体面。这里做成独立整页登录界面：
 *   - **正式入口常驻**：学校统一身份认证（CAS），就是一个跳转链接 `/api/v1/auth/cas/login`；
 *     非 cas 模式（如演示部署）也照常展示，只加一行说明，接入后即生效；
 *   - demo 模式下另给「进入演示身份」（内置样例数据，不读真实个人信息）作为备选；
 *   - anonymous 模式补充说明会话只存本浏览器。
 * 右上角 ✕ / 底部「先匿名体验」= 关掉登录页继续匿名使用（不强制登录）。
 */
export function LoginPage({
  config,
  busy,
  error,
  onDemoLogin,
  onClose,
}: {
  config: PublicConfig;
  busy: boolean;
  error?: string | null;
  onDemoLogin: () => void;
  onClose: () => void;
}) {
  // 非 cas 模式：后端对 /auth/cas/login 会返回 404 AUTH_MODE_DISABLED（设计如此），
  // 所以这里不跳转，只给一句说明，避免老师点出一个报错页。
  const [casNotice, setCasNotice] = useState(false);
  const managing = !!useOptionalSession()?.session?.jw_session;
  const casEnabled = config.auth_mode === "cas";
  /** 桌面客户端：直接打开学校统一认证窗口（返回 true 表示已处理） */
  const openDesktopLogin = (): boolean => {
    const bridge = (window as unknown as { xiaowoDesktop?: { loginJw?: () => void } }).xiaowoDesktop;
    if (bridge?.loginJw) { bridge.loginJw(); return true; }
    return false;
  };

  return (
    <div className="login-page" role="dialog" aria-modal="true" aria-label={managing ? "管理教务连接" : "登录小蛙助手"}>
      <div className="login-page__card">
        <header className="login-page__head">
          <Brand />
          <button type="button" className="login-page__close" onClick={onClose} aria-label={managing ? "关闭连接管理，返回小蛙助手" : "关闭登录页，先匿名使用"}>
            <X size={18} />
          </button>
        </header>

        <h1 className="login-page__title">{managing ? "管理教务连接" : "连接你的校园"}</h1>
        <p className="login-page__lead">{managing
          ? "已连接教务，可重新认证或断开连接。"
          : <>连接自己的课表、成绩与培养方案。</>}
        </p>

        {error && <p className="login-page__error" role="alert">{error}</p>}

        {/* 正式入口：学校统一身份认证（CAS）——就是一个跳转链接，常驻展示 */}
        <section className="login-page__option">
          <h2 className="login-page__option-title">中国科学技术大学 · 统一身份认证</h2>

          {casEnabled ? (
            <a className="login-page__primary" href="/api/v1/auth/cas/login"
              onClick={(e) => { if (openDesktopLogin()) e.preventDefault(); }}>
              <LogIn size={18} />
              统一认证登录
            </a>
          ) : (
            <button
              type="button"
              className="login-page__primary"
              onClick={() => { if (!openDesktopLogin()) setCasNotice(true); }}
            >
              <LogIn size={18} />
              统一认证登录
            </button>
          )}
          {!casEnabled && (
            <p className="login-page__note">
              <ShieldCheck size={14} />
              统一认证入口已预留。桌面客户端可直接连接学校账号，浏览器也可导入已有教务会话。
            </p>
          )}
          {casNotice && (
            <p className="login-page__notice" role="status">
              <Info size={14} />
              此浏览器暂未开通直接统一认证。请使用小蛙助手 App 或桌面客户端登录，也可在下方导入已有教务会话。
            </p>
          )}
        </section>

        {!managing && config.auth_mode === "demo" && (
          <>
            <div className="login-page__divider"><span>或</span></div>
            <section className="login-page__option">
              <h2 className="login-page__option-title">演示身份</h2>
              <p className="login-page__option-hint">使用内置样例数据，不读取任何真实个人信息</p>
              <button
                type="button"
                className="login-page__secondary"
                disabled={busy}
                onClick={onDemoLogin}
              >
                <LogIn size={18} />
                {busy ? "正在进入…" : "进入演示身份"}
              </button>
            </section>
          </>
        )}

        {!managing && config.auth_mode === "anonymous" && (
          <p className="login-page__note">
            <ShieldCheck size={14} />
            当前为匿名模式：会话仅保存在本浏览器，可随时退出。
          </p>
        )}

        <SsoImportPanel />
          <button type="button" className="login-page__ghost" onClick={onClose}>
          {managing ? "返回小蛙助手" : "先匿名体验"}
        </button>
      </div>
    </div>
  );
}
