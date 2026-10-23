/**
 * 「导入登录态」面板（方案 B，2026-10-23）
 *
 * 用户先用 scripts/sso_login_browser.mjs 在浏览器完成统一认证并导出 CookieJar，
 * 这里把 JSON 粘贴进来 → POST /api/v1/auth/sso_cookie → 服务端加密后写入
 * 我们域的 HttpOnly cookie（不落库）。也可以在这里断开。
 */
import { useEffect, useState } from "react";
import { KeyRound, Unplug } from "lucide-react";
import { apiGet, apiMutation } from "../lib/api";

type SessionPayload = { csrf_token: string; jw_session?: boolean };

export function SsoImportPanel() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [csrf, setCsrf] = useState("");
  const [linked, setLinked] = useState(false);
  const [msg, setMsg] = useState("");

  const refresh = async () => {
    try {
      const s = await apiGet<SessionPayload>("/auth/session");
      setCsrf(s.csrf_token);
      setLinked(Boolean(s.jw_session));
    } catch {
      /* 未登录时忽略 */
    }
  };
  useEffect(() => {
    void refresh();
  }, []);

  const doImport = async () => {
    setMsg("");
    try {
      const parsed = JSON.parse(text);
      const cookies = Array.isArray(parsed) ? parsed : parsed.cookies;
      if (!Array.isArray(cookies) || cookies.length === 0) throw new Error("JSON 里没有 cookies 数组");
      await apiMutation("/auth/sso_cookie", csrf, { method: "POST", body: JSON.stringify({ cookies }) });
      setLinked(true);
      setText("");
      setMsg("导入成功：教务已连接");
    } catch (e) {
      setMsg(`导入失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const doClear = async () => {
    setMsg("");
    try {
      await apiMutation("/auth/sso_cookie", csrf, { method: "DELETE", body: "{}" });
      setLinked(false);
      setMsg("已断开教务会话");
    } catch (e) {
      setMsg(`断开失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div style={{ marginTop: 18, borderTop: "1px solid rgba(0,0,0,.08)", paddingTop: 14 }}>
      <button type="button" className="login-page__ghost" onClick={() => setOpen(v => !v)}>
        <KeyRound size={16} /> 已用统一认证登录？导入登录态
      </button>
      <p style={{ fontSize: 13, opacity: 0.7, marginTop: 6 }}>
        教务：{linked ? "已连接" : "未连接"}（会话保存在你自己的浏览器里，服务端不落库）
      </p>
      {open && (
        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder='粘贴 sso_login_browser.mjs 生成的 JSON（含 "cookies" 数组）'
            rows={5}
            style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" onClick={doImport} disabled={!text.trim() || !csrf}>导入</button>
            <button type="button" onClick={doClear} disabled={!linked || !csrf}>
              <Unplug size={14} /> 断开
            </button>
          </div>
          {msg && <p style={{ fontSize: 13 }}>{msg}</p>}
        </div>
      )}
    </div>
  );
}
