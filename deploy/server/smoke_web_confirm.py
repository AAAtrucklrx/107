"""确认态联网问答冒烟：登录 demo -> mode=web 提问 -> SSE 提取回答与 claims。"""
import json, sys, urllib.request, http.cookiejar, time

BASE = "http://114.214.241.119:8850"
ORIGIN = "http://114.214.241.119:8850"
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "中国科学技术大学教务处官网的选课通知发布在哪里？"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def req(path, method="GET", body=None, headers=None, timeout=150):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Origin", ORIGIN)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    with opener.open(r, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())

def main() -> int:
    st, sess = req("/api/v1/auth/session")
    csrf = sess.get("csrf_token", "")
    st, sess = req("/api/v1/auth/demo", "POST", {}, {"X-CSRF-Token": csrf})
    if st != 200:
        print(json.dumps({"stage": "login", "status": st, "body": sess}, ensure_ascii=False))
        return 1
    st, run = req("/api/v1/chat/runs", "POST",
                  {"question": QUESTION, "mode": "web"},
                  {"X-CSRF-Token": sess.get("csrf_token", csrf)})
    run_id = run["run_id"]
    r = urllib.request.Request(BASE + f"/api/v1/chat/runs/{run_id}/events")
    r.add_header("Origin", ORIGIN)
    ev = {}
    md = []
    stage = set()
    claims = None
    failed = None
    t0 = time.time()
    with opener.open(r, timeout=170) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode().strip()
            if line == "":
                if ev:
                    t = ev.get("type")
                    payload = ev.get("data", {})
                    inner = payload.get("data", payload) if isinstance(payload, dict) else payload
                    if t == "stage.changed" and isinstance(inner, dict):
                        stage.add(inner.get("stage", ""))
                    if t == "answer.segment" and isinstance(inner, dict) and inner.get("markdown"):
                        md.append(inner["markdown"])
                    if t == "answer.completed" and isinstance(inner, dict):
                        claims = inner.get("claims")
                    if t in ("run.failed", "run.cancelled"):
                        failed = t
                    ev = {}
            elif line.startswith("event: "):
                ev["type"] = line[7:]
            elif line.startswith("data: "):
                try:
                    ev["data"] = json.loads(line[6:])
                except Exception:
                    ev["data"] = line[6:]
    full = "".join(md)
    out = {
        "run_id": run_id,
        "elapsed_s": round(time.time() - t0, 1),
        "stages": sorted(stage),
        "failed": failed,
        "answer_chars": len(full),
        "answer_head": full[:240],
        "claims": [
            {"status": c.get("status"), "text": (c.get("text") or "")[:80],
             "evidence": [e.get("source_id") for e in (c.get("evidence") or [])]}
            for c in (claims or [])
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    statuses = {c.get("status") for c in (claims or [])}
    return 0 if ("confirmed" in statuses or failed is None) else 2

if __name__ == "__main__":
    sys.exit(main())
