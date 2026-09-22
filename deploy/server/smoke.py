import json, time, urllib.request, http.cookiejar, sys

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://114.214.241.119:8850"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def req(path, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Origin", ORIGIN)
    for k, v in (headers or {}).items(): r.add_header(k, v)
    try:
        with opener.open(r, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

def sse_events(path, timeout=180):
    """读取 SSE 流，yield 每个 event dict，直到流结束或超时。"""
    r = urllib.request.Request(BASE + path)
    r.add_header("Origin", ORIGIN)
    ev = {}
    start = time.time()
    with opener.open(r, timeout=timeout) as resp:
        while True:
            if time.time() - start > timeout:
                raise TimeoutError("SSE 超时")
            line = resp.readline()
            if not line:
                break
            line = line.decode().strip()
            if line == "":
                if ev:
                    yield ev
                    ev = {}
            elif line.startswith("event: "):
                ev["type"] = line[7:]
            elif line.startswith("data: "):
                try: ev["data"] = json.loads(line[6:])
                except Exception: ev["data"] = line[6:]
        if ev:
            yield ev

# 1. 匿名会话 -> csrf
st, sess = req("/api/v1/auth/session")
csrf = sess.get("csrf_token", "")
print("session: 200 csrf OK")

# 2. demo 登录
st, sess = req("/api/v1/auth/demo", "POST", {}, {"X-CSRF-Token": csrf})
if st != 200:
    print("login FAILED:", sess); sys.exit(1)
p = sess.get("principal", {})
print(f"login OK: {p.get('id')} / {p.get('profile',{}).get('name')} / admin={p.get('is_admin')}")

# 3. 创建 run
st, run = req("/api/v1/chat/runs", "POST",
              {"question": "学生证丢了怎么办？", "mode": "auto"},
              {"X-CSRF-Token": sess.get("csrf_token", csrf)})
if st not in (200, 201):
    print("create_run FAILED:", run); sys.exit(1)
run_id = run["run_id"]
print(f"create_run OK: {run_id} effective={run.get('effective_mode')}")

# 4. SSE 流式读取到终态
terminal = None
for ev in sse_events(f"/api/v1/chat/runs/{run_id}/events"):
    t = ev.get("type")
    if t in ("answer.completed", "run.failed", "run.cancelled"):
        terminal = ev
        break
if terminal is None:
    print("TIMEOUT"); sys.exit(3)
print("TERMINAL:", terminal.get("type"))
if terminal.get("type") == "answer.completed":
    a = terminal.get("data", {})
    content = a.get("content") or a.get("answer") or ""
    print("ANSWER:", content[:700])
    src = a.get("sources") or []
    print("SOURCES:", len(src), json.dumps(src[:2], ensure_ascii=False)[:200])
    sys.exit(0)
else:
    print(json.dumps(terminal, ensure_ascii=False)[:500]); sys.exit(2)
