#!/usr/bin/env python3
"""小蜗运维台 Web 服务（2026-09-29）。

把 `ops_dashboard.py` 的仪表盘做成常驻 HTTP 服务，便于端口转发后从外部查看。
只读：不提供任何启停/写操作接口。默认**强制 Basic 认证**（运维台会暴露服务/日志/使用量信息）。

    python deploy/server/ops_web.py                 # 0.0.0.0:8899，带认证，快照缓存 15s
    python deploy/server/ops_web.py --port 9000 --interval 30
    python deploy/server/ops_web.py --host 127.0.0.1   # 只本机（配 SSH 隧道时用）
    python deploy/server/ops_web.py --no-auth         # 仅建议在 127.0.0.1 上使用
    python deploy/server/ops_web.py --print-credentials  # 打印当前账号（不改动服务）

路由：`/`（仪表盘，自动刷新）、`/onepager`（运维一页纸）、`/json`（原始快照）、`/health`（存活）。
凭据：优先环境变量 `XIAOWO_OPS_USER` / `XIAOWO_OPS_PASS`，其次 `deploy/server/run/ops_web.env`；
都没有时自动生成一个随机密码并写入该文件（600）。
"""

from __future__ import annotations

import argparse
import base64
import hmac
import http.server
import json
import os
import secrets
import socketserver
import ssl
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "deploy" / "server" / "run"
CRED_FILE = RUN_DIR / "ops_web.env"
sys.path.insert(0, str(ROOT / "deploy" / "server"))
import ops_collect  # noqa: E402
import ops_dashboard  # noqa: E402
import ops_onepager  # noqa: E402

DEFAULT_USER = "xiaowo"


def load_credentials(create: bool = True) -> tuple[str, str, bool]:
    """返回 (user, password, generated)。"""
    user = (os.environ.get("XIAOWO_OPS_USER") or "").strip()
    pwd = (os.environ.get("XIAOWO_OPS_PASS") or "").strip()
    if user and pwd:
        return user, pwd, False
    if CRED_FILE.is_file():
        data = {}
        for line in CRED_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
        user = user or data.get("XIAOWO_OPS_USER", DEFAULT_USER)
        pwd = pwd or data.get("XIAOWO_OPS_PASS", "")
        if pwd:
            return user, pwd, False
    if not create:
        return user or DEFAULT_USER, "", False
    user = user or DEFAULT_USER
    pwd = secrets.token_urlsafe(12)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    _write_cred_file(user, pwd)
    return user, pwd, True


def _write_cred_file(user: str, pwd: str) -> None:
    secret = _session_secret(create=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(
        f"XIAOWO_OPS_USER={user}\nXIAOWO_OPS_PASS={pwd}\nXIAOWO_OPS_SECRET={secret}\n",
        encoding="utf-8")
    CRED_FILE.chmod(0o600)


def _read_cred() -> dict:
    data: dict[str, str] = {}
    if CRED_FILE.is_file():
        for line in CRED_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def _cfg(key: str, default: str = "") -> str:
    """配置读取：环境变量优先，其次 run/ops_web.env（KEY=VALUE）。

    这样运维台的所有开关（端口/TLS/证书路径/TTL）都能写在同一个文件里，
    改完 `ops.sh restart ops_web` 即生效，不必去改 start_all.sh。
    """
    value = (os.environ.get(key) or "").strip()
    if value:
        return value
    return (str(_read_cred().get(key) or default)).strip()


def _cfg_flag(key: str, default: bool = False) -> bool:
    raw = _cfg(key, "1" if default else "0").casefold()
    return raw in ("1", "true", "yes", "on")


def _session_secret(create: bool = True) -> str:
    secret = (os.environ.get("XIAOWO_OPS_SECRET") or _read_cred().get("XIAOWO_OPS_SECRET") or "").strip()
    if secret or not create:
        return secret
    secret = secrets.token_urlsafe(24)
    if CRED_FILE.is_file():  # 只补写密钥，不动账号密码
        content = CRED_FILE.read_text(encoding="utf-8", errors="replace").rstrip("\n")
        CRED_FILE.write_text(content + f"\nXIAOWO_OPS_SECRET={secret}\n", encoding="utf-8")
        CRED_FILE.chmod(0o600)
    return secret


LOGIN_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小蜗 · 运维台登录</title><style>
:root{--ink:#16222d;--muted:#61707d;--line:#d6dee6;--bg:#f4f7fa}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(160deg,#034ea1 0%,#0a6bc4 45%,#eef3f7 45%,#f4f7fa 100%);
  font:14px/1.6 "Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink)}
.card{width:min(92vw,380px);background:#fff;border-radius:14px;box-shadow:0 18px 50px rgb(6 40 75 / .25);
  padding:28px 26px 22px}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.logo{width:34px;height:34px;border-radius:10px;background:#034ea1;color:#fff;display:flex;
  align-items:center;justify-content:center;font-weight:700}
h1{font-size:17px;margin:0}
.sub{color:var(--muted);font-size:12px;margin:2px 0 18px}
label{display:block;font-size:12px;color:var(--muted);margin:10px 0 4px}
input{width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;font-size:14px;
  background:#fbfdff}
input:focus{outline:2px solid #0a6bc4;outline-offset:1px}
button{width:100%;margin-top:16px;padding:10px;border:0;border-radius:8px;background:#034ea1;color:#fff;
  font-size:14px;font-weight:600;cursor:pointer}
button:hover{background:#023f83}
.err{color:#c0392b;font-size:12px;margin:8px 0 0}
.foot{color:var(--muted);font-size:11px;margin-top:16px;border-top:1px solid var(--line);padding-top:10px}
</style></head><body>
<form class="card" method="post" action="/login" autocomplete="off">
  <div class="brand"><span class="logo">蜗</span><h1>小蜗运维台</h1></div>
  <p class="sub">仅限运维人员访问 · 只读监控面板</p>
  <label for="username">账号</label>
  <input id="username" name="username" autocomplete="username" required>
  <label for="password">密码</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <!--ERR-->
  <button type="submit">登 录</button>
  <p class="foot">登录后可查看：服务状态 · 健康门槛 · 出网与校园网 · 备份 · 资源 · 最近错误<br>
  账号在服务器 <code>deploy/server/run/ops_web.env</code> 中维护。</p>
</form></body></html>"""


class _Cache:
    """快照/HTML 的短 TTL 缓存：多人同时刷页面时不重复采集。"""

    def __init__(self, ttl: float) -> None:
        self.ttl = max(3.0, ttl)
        self.lock = threading.RLock()  # 可重入：dashboard() 持锁期间会再调 snapshot()
        self.snap: dict | None = None
        self.snap_at = 0.0
        self.html = ""
        self.html_at = 0.0
        self.onepager = ""
        self.onepager_at = 0.0

    def snapshot(self) -> dict:
        with self.lock:
            if self.snap is None or time.time() - self.snap_at > self.ttl:
                self.snap = ops_collect.snapshot()
                self.snap_at = time.time()
            return self.snap

    def dashboard(self, refresh: int) -> str:
        if not self.html or time.time() - self.html_at > self.ttl:
            snap = self.snapshot()  # 采集在锁外，避免拖住其他请求
            with self.lock:
                if not self.html or time.time() - self.html_at > self.ttl:
                    self.html = ops_dashboard.render(snap, refresh=refresh)
                    self.html_at = time.time()
        return self.html

    def page_onepager(self) -> str:
        if not self.onepager or time.time() - self.onepager_at > max(self.ttl, 60.0):
            _, page = ops_onepager.build()
            with self.lock:
                self.onepager, self.onepager_at = page, time.time()
        return self.onepager


SESSION_TTL = 12 * 3600
LOGOUT_MARK = RUN_DIR / "ops_web_logout_epoch"


def _logout_epoch() -> float:
    try:
        return float(LOGOUT_MARK.read_text(encoding="utf-8").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _mark_logout() -> None:
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        LOGOUT_MARK.write_text(f"{int(time.time())}\n", encoding="utf-8")  # 截断，与签发口径一致
    except Exception:  # noqa: BLE001
        pass


def make_handler(cache: _Cache, creds: tuple[str, str, bool], auth_required: bool,
                 refresh: int) -> type[http.server.BaseHTTPRequestHandler]:
    user, pwd, _gen = creds
    secret = _session_secret(create=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "xiaowo-ops/1.0"

        # ── 认证：会话 cookie 优先，Basic 认证兼容（脚本/隧道）──
        def _cookie_token(self) -> str:
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "ops_session":
                    return v
            return ""

        def _cookie_valid(self) -> bool:
            token = self._cookie_token()
            if not token or "." not in token or not secret:
                return False
            issued_s, _, sig = token.partition(".")
            try:
                issued = int(issued_s)
            except ValueError:
                return False
            want = hmac.new(secret.encode(), issued_s.encode(), "sha256").hexdigest()
            if not hmac.compare_digest(sig, want):
                return False
            now = time.time()
            if not (issued <= now < issued + SESSION_TTL):
                return False
            return issued + 1 >= _logout_epoch()  # 容忍 1s 取整偏差

        def _basic_valid(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                raw = base64.b64decode(header[6:]).decode("utf-8")
            except Exception:  # noqa: BLE001
                return False
            got_user, _, got_pwd = raw.partition(":")
            return hmac.compare_digest(got_user, user) and hmac.compare_digest(got_pwd, pwd)

        def _authorized(self) -> bool:
            if not auth_required:
                return True
            return self._cookie_valid() or self._basic_valid()

        def _new_session_cookie(self) -> str:
            issued = int(time.time())
            sig = hmac.new(secret.encode(), str(issued).encode(), "sha256").hexdigest()
            return f"ops_session={issued}.{sig}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"

        def _redirect(self, location: str, cookie: str = "") -> None:
            self.send_response(302)
            self.send_header("Location", location)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler 约定
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/health":
                self._send(200, json.dumps({"status": "ok"}).encode(), "application/json; charset=utf-8")
                return
            if path == "/login":
                if self._cookie_valid():
                    self._redirect("/")
                else:
                    self._send(200, LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/logout":
                _mark_logout()  # 服务端吊销：所有旧会话立即失效
                self._redirect("/login", cookie="ops_session=; Path=/; Max-Age=0")
                return
            if not self._authorized():
                # 浏览器走登录页；脚本（Basic 头）保持 401
                if self.headers.get("Authorization"):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="xiaowo-ops"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._redirect("/login")
                return
            try:
                if path == "/":
                    self._send(200, cache.dashboard(refresh).encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/onepager":
                    self._send(200, cache.page_onepager().encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/json":
                    body = json.dumps(cache.snapshot(), ensure_ascii=False, indent=1).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                else:
                    self._send(404, "not found".encode(), "text/plain; charset=utf-8")
            except BrokenPipeError:
                pass
            except Exception as e:  # noqa: BLE001 — 单次请求失败不该影响服务
                self._send(500, f"采集失败：{type(e).__name__}: {e}".encode("utf-8"),
                           "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path != "/login":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                body = ""
            from urllib.parse import parse_qs
            form = parse_qs(body)
            got_user = (form.get("username") or [""])[0]
            got_pwd = (form.get("password") or [""])[0]
            if hmac.compare_digest(got_user, user) and hmac.compare_digest(got_pwd, pwd):
                self._redirect("/", cookie=self._new_session_cookie())
            else:
                page = LOGIN_PAGE.replace("<!--ERR-->",
                                          '<p class="err">账号或密码不正确，请重试。</p>')
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def log_message(self, fmt: str, *args) -> None:  # 精简日志
            sys.stderr.write("[%s] %s %s\n" % (time.strftime("%H:%M:%S"),
                                               self.address_string(), fmt % args))

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="小蜗运维台 Web 服务")
    ap.add_argument("--host", default=_cfg("XIAOWO_OPS_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(_cfg("XIAOWO_OPS_PORT", "8899")))
    ap.add_argument("--interval", type=float, default=float(_cfg("XIAOWO_OPS_TTL", "30")),
                    help="快照缓存与页面刷新秒数（默认 30）")
    ap.add_argument("--no-auth", action="store_true", help="关闭认证（仅建议绑定 127.0.0.1）")
    ap.add_argument("--print-credentials", action="store_true", help="打印当前账号后退出")
    ap.add_argument("--tls", action="store_true", default=_cfg_flag("XIAOWO_OPS_TLS"),
                    help="启用 HTTPS（自签证书；证书路径见 --cert/--key）")
    ap.add_argument("--cert", default=_cfg("XIAOWO_OPS_CERT", str(RUN_DIR / "ops_web_cert.pem")))
    ap.add_argument("--key", default=_cfg("XIAOWO_OPS_KEY", str(RUN_DIR / "ops_web_key.pem")))
    ap.add_argument("--init-tls", action="store_true",
                    help="生成自签证书（含 IP SAN）后退出，供 --tls 使用")
    args = ap.parse_args()

    if args.init_tls:
        cert, key = Path(args.cert), Path(args.key)
        cert.parent.mkdir(parents=True, exist_ok=True)
        import subprocess
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
            "-keyout", str(key), "-out", str(cert),
            "-subj", f"/CN={args.host}", "-addext", f"subjectAltName=IP:{args.host},DNS:localhost",
        ], check=True, capture_output=True)
        key.chmod(0o600)
        print(f"[tls] 已生成自签证书：{cert}（私钥 {key}，825 天）")
        return 0

    creds = load_credentials(create=not args.print_credentials)
    user, pwd, generated = creds
    if args.print_credentials:
        print(f"user={user}\npass={pwd}\nfile={CRED_FILE}")
        return 0

    cache = _Cache(args.interval)
    handler = make_handler(cache, creds, auth_required=not args.no_auth, refresh=int(args.interval))

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server((args.host, args.port), handler)
    scheme = "http"
    if args.tls:
        cert, key = Path(args.cert), Path(args.key)
        if not (cert.is_file() and key.is_file()):
            print(f"[ops_web] 未找到证书（{cert} / {key}）：先跑 ops_web.py --init-tls 生成")
            return 2
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    banner = (f"[ops_web] {scheme}://{args.host}:{args.port}/ 认证={'开' if not args.no_auth else '关'}"
              f" 缓存={args.interval}s 账号={user}"
              + ("（本次自动生成，已存 " + str(CRED_FILE) + "）" if generated else ""))
    print(banner, flush=True)
    if generated:
        print(f"[ops_web] 初始密码：{pwd}（可改 {CRED_FILE} 后重启）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[ops_web] 收到中断，退出", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
