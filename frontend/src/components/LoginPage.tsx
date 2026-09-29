import { useState } from "react";
import { Info, LogIn, ShieldCheck, X } from "lucide-react";
import { Brand } from "./Brand";
import type { PublicConfig } from "../types";

/**
 * 小蜗「正式登录页」（2026-09-29）。
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
  const casEnabled = config.auth_mode === "cas";

  return (
    <div className="login-page" role="dialog" aria-modal="true" aria-label="登录小蜗">
      <div className="login-page__card">
        <header className="login-page__head">
          <Brand />
          <button type="button" className="login-page__close" onClick={onClose} aria-label="关闭登录页，先匿名使用">
            <X size={18} />
          </button>
        </header>

        <h1 className="login-page__title">登录后使用完整功能</h1>
        <p className="login-page__lead">
          课表、成绩、考试、培养方案、课程推荐与评课都跟你的身份绑定；登录只用于查询
          <strong>你自己的</strong>校内信息。
        </p>

        <ul className="login-page__features">
          <li>个人课表 · 成绩 · 考试安排 · 培养方案</li>
          <li>课程推荐 · 评课与教师评分 · 选课冲突</li>
          <li>校园问答（联网核实并标注来源）</li>
        </ul>

        {error && <p className="login-page__error" role="alert">{error}</p>}

        {/* 正式入口：学校统一身份认证（CAS）——就是一个跳转链接，常驻展示 */}
        <section className="login-page__option">
          <h2 className="login-page__option-title">中国科学技术大学 · 统一身份认证</h2>
          <p className="login-page__option-hint">用学号与统一身份认证密码登录（正式入口）</p>
          {casEnabled ? (
            <a className="login-page__primary" href="/api/v1/auth/cas/login">
              <LogIn size={18} />
              统一认证登录
            </a>
          ) : (
            <button
              type="button"
              className="login-page__primary"
              onClick={() => setCasNotice(true)}
            >
              <LogIn size={18} />
              统一认证登录
            </button>
          )}
          {!casEnabled && (
            <p className="login-page__note">
              <ShieldCheck size={14} />
              当前部署为演示模式：统一认证入口已预留，正式部署切换后即可直接使用。
            </p>
          )}
          {casNotice && (
            <p className="login-page__notice" role="status">
              <Info size={14} />
              正式部署时把 <code>XIAOWO_AUTH_MODE</code> 切到 <code>cas</code> 并配置
              <code> CAS_SERVICE_URL</code>，这个按钮就会真正跳转到学校统一认证页面。
            </p>
          )}
        </section>

        {config.auth_mode === "demo" && (
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

        {config.auth_mode === "anonymous" && (
          <p className="login-page__note">
            <ShieldCheck size={14} />
            当前为匿名模式：会话仅保存在本浏览器，可随时退出。
          </p>
        )}

        <button type="button" className="login-page__ghost" onClick={onClose}>
          先匿名体验
        </button>
      </div>
    </div>
  );
}
