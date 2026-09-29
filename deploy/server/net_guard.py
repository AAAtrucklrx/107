#!/usr/bin/env python3
"""小蜗出网守卫（2026-09-29）。

背景：2026-09-27 校园网「网络通」权限失效，这台服务器被门户自动降级成「校内」，
校外 HTTPS 全部不通 → LLM 与搜索不可达 → 小蜗静默降级了两天才被发现。
本脚本把这类故障变成"自动体检 + 自动重开 + 留痕告警"：

  每 5 分钟（默认）探一次校外出网；
  发现不通时查校园门户当前的「出口 / 权限」，若权限不是国际/完全访问，则用已保存的凭据
  重新提交开通（1教育网出口 · 国际 · 永久），再复检；
  结果写 deploy/server/run/net_state.json（仪表盘读它）；
  仍不通则写 run/net_alert.txt 告警（若配置了 XIAOWO_OPS_WEBHOOK 还会 POST 一条消息）。

用法：
  python deploy/server/net_guard.py --once                # 单次体检（不改动，除必要时重开）
  python deploy/server/net_guard.py --once --no-reactivate # 只看不修
  python deploy/server/net_guard.py                       # 常驻循环（start_all.sh 拉起）
凭据：/root/.wlt_name 与 /root/.wlt_pass（600）；可用 XIAOWO_WLT_NAME_FILE / XIAOWO_WLT_PASS_FILE 覆盖。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "deploy" / "server" / "run"
PORTAL = "http://wlt.ustc.edu.cn/cgi-bin/ip"
NAME_FILE = Path(os.environ.get("XIAOWO_WLT_NAME_FILE", "/root/.wlt_name"))
PASS_FILE = Path(os.environ.get("XIAOWO_WLT_PASS_FILE", "/root/.wlt_pass"))
INTERVAL = int(os.environ.get("XIAOWO_NET_GUARD_INTERVAL", "300"))
EXIT_TYPE = os.environ.get("XIAOWO_NET_EXIT_TYPE", "0")   # 0 = 1教育网出口(国际)
EXPIRE = os.environ.get("XIAOWO_NET_EXPIRE", "0")         # 0 = 永久
PROBE_URLS = tuple(u.strip() for u in os.environ.get(
    "XIAOWO_NET_PROBE_URLS", "https://api.deepseek.com,https://www.baidu.com").split(",") if u.strip())
FULL_SCOPES = ("国际", "完全访问")


def probe(timeout: float = 6.0) -> tuple[bool, str, int]:
    """校外可达性：4xx 也算通（连接已建立）。返回 (ok, 说明, 耗时ms)。"""
    t0 = time.time()
    for url in PROBE_URLS:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=timeout):  # noqa: S310
                return True, url, int((time.time() - t0) * 1000)
        except urllib.error.HTTPError as e:  # 401/404 等 → 网络是通的
            return True, f"{url} (HTTP {e.code})", int((time.time() - t0) * 1000)
        except Exception:  # noqa: BLE001
            continue
    return False, "全部探测目标超时", int((time.time() - t0) * 1000)


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return ""


def _credentials() -> tuple[str, str]:
    name = NAME_FILE.read_text(encoding="utf-8").strip() if NAME_FILE.is_file() else ""
    pwd = PASS_FILE.read_text(encoding="utf-8").strip() if PASS_FILE.is_file() else ""
    return name, pwd


def portal_status() -> dict:
    """登录门户并解析当前 IP 的出口/权限（仅在出网异常时调用，避免刷门户日志）。"""
    name, pwd = _credentials()
    if not name or not pwd:
        return {"known": False, "error": "缺少凭据文件"}
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({
        "cmd": "login", "url": "URL", "ip": local_ip(), "name": name, "password": pwd,
    }).encode()
    try:
        with opener.open(PORTAL, data=data, timeout=12) as resp:
            text = resp.read().decode("gbk", "replace")
    except Exception as e:  # noqa: BLE001
        return {"known": False, "error": f"{type(e).__name__}: {e}"}
    scope = re.search(r"权限[:：]\s*([^\s<]+)", text)
    exit_ = re.search(r"出口[:：]\s*([^<\n]+)", text)
    return {
        "known": True,
        "exit": (exit_.group(1).strip() if exit_ else ""),
        "scope": (scope.group(1).strip() if scope else ""),
        "message": ("网络设置成功" if "网络设置成功" in text else ""),
    }


def reactivate() -> tuple[bool, str]:
    """按原配置重新开通：1教育网出口(国际) · 永久。"""
    name, pwd = _credentials()
    if not name or not pwd:
        return False, "缺少凭据文件，无法自动重开"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        login = urllib.parse.urlencode({
            "cmd": "login", "url": "URL", "ip": local_ip(), "name": name, "password": pwd,
        }).encode()
        opener.open(PORTAL, data=login, timeout=12).read()
        go = urllib.parse.quote(" 开通网络 ".encode("gbk"))
        url = f"{PORTAL}?cmd=set&url=URL&type={EXIT_TYPE}&exp={EXPIRE}&go={go}"
        with opener.open(url, timeout=15) as resp:
            text = resp.read().decode("gbk", "replace")
        ok = "网络设置成功" in text or "权限: 国际" in text.replace("：", ":")
        return ok, ("已提交重新开通" if ok else "重新开通未生效（请人工登录门户确认权限）")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _write_state(state: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "net_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _alert(message: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "net_alert.txt").write_text(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n", encoding="utf-8")
    hook = os.environ.get("XIAOWO_OPS_WEBHOOK", "").strip()
    if not hook:
        try:  # 允许把 webhook 写进 .env，避免明文出现在命令行
            for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("XIAOWO_OPS_WEBHOOK="):
                    hook = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:  # noqa: BLE001
            hook = ""
    if hook:
        try:
            body = json.dumps({"text": f"[小蜗] {message}"}).encode()
            req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).read()  # noqa: S310 — 由运维自行配置
        except Exception:  # noqa: BLE001
            pass


def cycle(reactivate_allowed: bool = True) -> dict:
    ok, detail, cost = probe()
    state = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": ok, "detail": detail, "latency_ms": cost,
        "fail_streak": 0, "action": "", "alert": "",
    }
    if not ok:
        info = portal_status()
        state.update({k: v for k, v in info.items() if k in ("exit", "scope")})
        state["known"] = bool(info.get("known"))
        if info.get("known") and info.get("scope") and not any(s in info["scope"] for s in FULL_SCOPES) \
                and reactivate_allowed:
            done, msg = reactivate()
            time.sleep(2)
            ok2, detail2, cost2 = probe()
            state.update({"action": "reactivate", "action_result": msg,
                          "ok": ok2, "detail": detail2, "latency_ms": cost2})
            if ok2:
                state["exit"], state["scope"] = "1教育网出口", "国际"
        if not state["ok"]:
            prev = 0
            path = RUN_DIR / "net_state.json"
            if path.is_file():
                try:
                    prev = int(json.loads(path.read_text(encoding="utf-8")).get("fail_streak") or 0)
                except Exception:  # noqa: BLE001
                    prev = 0
            state["fail_streak"] = prev + 1
            state["alert"] = (f"校外出网不通（连续 {state['fail_streak']} 次）：{detail}；"
                              f"网络通权限={state.get('scope') or '未知'}；"
                              f"已尝试= {state.get('action_result') or '未尝试'}")
            _alert(state["alert"])
    else:
        # 正常时保留上次已知的门户出口/权限，便于仪表盘展示"当前出口 / 权限"
        prev_state = {}
        state_path = RUN_DIR / "net_state.json"
        if state_path.is_file():
            try:
                prev_state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                prev_state = {}
        for key in ("exit", "scope", "known"):
            if prev_state.get(key):
                state[key] = prev_state[key]
        if not state.get("known"):
            # 启动后首次（或状态被覆盖后）补查一次门户，让仪表盘能常显「出口 / 权限」；
            # 之后只在出网异常时才查，避免刷门户登录日志
            info = portal_status()
            if info.get("known"):
                state["known"] = True
                state["exit"] = info.get("exit") or ""
                state["scope"] = info.get("scope") or ""
        (RUN_DIR / "net_alert.txt").unlink(missing_ok=True)
    _write_state(state)
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="小蜗出网守卫")
    ap.add_argument("--once", action="store_true", help="只跑一次")
    ap.add_argument("--no-reactivate", action="store_true", help="只体检，不自动重新开通")
    ap.add_argument("--interval", type=int, default=INTERVAL)
    args = ap.parse_args()
    allow = not args.no_reactivate
    while True:
        st = cycle(allow)
        mark = "[ok]" if st["ok"] else "[FAIL]"
        extra = f" 动作={st.get('action_result')}" if st.get("action") else ""
        print(f"{mark} 出网: ok={st['ok']} ({st['detail']}, {st['latency_ms']}ms)"
              f" 权限={st.get('scope') or '-'} 连续失败={st['fail_streak']}{extra}", flush=True)
        if args.once:
            return 0 if st["ok"] else 1
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
