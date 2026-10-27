export function resolveMobileApiBase(base: string | undefined, mode: string, debug: string | undefined): string {
  if (!base) throw new Error("未配置小蛙助手服务地址，请安装已配置服务的 App。");
  let url: URL;
  try { url = new URL(base); } catch { throw new Error("小蛙助手服务地址格式无效。"); }
  const localDebug = mode === "mobile-debug" && debug === "1" &&
    ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && localDebug)) || url.username || url.password || url.search || url.hash || url.pathname.replace(/\/$/, "") !== "/api/v1") {
    throw new Error("服务地址必须为安全 HTTPS；本地 HTTP 仅限调试版回环地址。");
  }
  return url.href.replace(/\/$/, "");
}

export function mobileDeepLinkPath(value: string): "/" | "/academic" | "/campus" | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "xiaowo:" || url.hostname !== "app" || url.port || url.username || url.password || url.search || url.hash) return null;
    if (url.pathname === "" || url.pathname === "/" || url.pathname === "/chat") return "/";
    if (url.pathname === "/academic" || url.pathname === "/campus") return url.pathname;
  } catch { /* Ignore malformed external links. */ }
  return null;
}
