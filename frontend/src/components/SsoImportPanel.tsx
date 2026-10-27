/**
 * 「导入登录态」面板（方案 B，2026-10-23）
 *
 * 用户先用 scripts/sso_login_browser.mjs 在浏览器完成统一认证并导出 CookieJar，
 * 这里把 JSON 粘贴进来 → POST /api/v1/auth/sso_cookie → 服务端加密后写入
 * 我们域的 HttpOnly cookie（不落库）。也可以在这里断开。
 */
import { useState } from "react";
import { KeyRound, Unplug } from "lucide-react";
import { apiMutation } from "../lib/api";
import { useOptionalSession } from "../session/SessionProvider";

export function SsoImportPanel() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const context = useOptionalSession();
  const csrf = context?.session?.csrf_token ?? "";
  const linked = !!context?.session?.jw_session;
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const doImport = async () => {
    setMsg("");
    setBusy(true);
    try {
      const parsed = JSON.parse(text);
      const cookies = Array.isArray(parsed) ? parsed : parsed.cookies;
      if (!Array.isArray(cookies) || cookies.length === 0) throw new Error("JSON 里没有 cookies 数组");
      await apiMutation("/auth/sso_cookie", csrf, { method: "POST", body: JSON.stringify({ cookies }) });
      await context?.reload();
      setText("");
      setMsg("导入成功：教务已连接");
      context?.closeLogin();
    } catch (e) {
      setMsg(`导入失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  };

  const doClear = async () => {
    setMsg("");
    setBusy(true);
    try {
      await apiMutation("/auth/sso_cookie", csrf, { method: "DELETE", body: "{}" });
      await context?.reload();
      setText("");
      setMsg("已断开教务会话");
    } catch (e) {
      setMsg(`断开失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  };

  return (
    <section className="sso-import-panel" aria-label="教务会话连接">
      <button type="button" className="login-page__primary" onClick={() => {
        const bridge = (window as unknown as { xiaowoDesktop?: { loginJw?: () => void } }).xiaowoDesktop;
        if (bridge?.loginJw) { bridge.loginJw(); return; }   // Electron：打开学校登录窗
        setOpen(v => !v);                                   // 浏览器：展开手动导入
      }}>
        <KeyRound size={16} /> {linked ? "重新连接学校账号" : "用学校账号登录"}
      </button>
      <button type="button" className="login-page__ghost" onClick={() => setOpen(v => !v)}>
        <KeyRound size={16} /> 已有登录态？导入教务会话
      </button>
      <p className="sso-import-note">
        教务：{linked ? "已连接" : "未连接"}（会话保存在你自己的浏览器里，服务端不落库）
      </p>
      {open && (
        <div className="sso-import-fields">
          <textarea
            aria-label="教务会话 JSON"
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder='粘贴 sso_login_browser.mjs 生成的 JSON（含 "cookies" 数组）'
            rows={5}
            className="sso-import-text"
          />
          <div className="sso-import-actions">
            <button type="button" onClick={doImport} disabled={busy || !text.trim() || !csrf}>导入</button>
          </div>
        </div>
      )}
      {linked && <button type="button" className="login-page__ghost" onClick={doClear} disabled={busy || !csrf}><Unplug size={14} />断开教务连接</button>}
      {msg && <p className="sso-import-note" role="status">{msg}</p>}
    </section>
  );
}
