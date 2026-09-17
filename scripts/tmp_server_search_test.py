import time, json, requests

BASE = "http://114.214.241.119:8850"
s = requests.Session()
s.headers["User-Agent"] = "xiaowo-e2e"

# 1) 匿名会话 + CSRF
r = s.get(f"{BASE}/api/v1/auth/session", timeout=15)
csrf = r.json()["csrf_token"]
# 2) 进入演示身份
r = s.post(f"{BASE}/api/v1/auth/demo", headers={"Origin": BASE, "X-CSRF-Token": csrf}, timeout=15)
print("demo登录:", r.status_code)
# 3) 取新 csrf
csrf = s.get(f"{BASE}/api/v1/auth/session", timeout=15).json()["csrf_token"]
# 4) 发起真实对话（触发联网搜索管线）
r = s.post(f"{BASE}/api/v1/chat/runs", headers={"Origin": BASE, "X-CSRF-Token": csrf},
           json={"question": "中国科学技术大学有哪些官方网站？比如教务处、图书馆、研究生院", "requested_mode": "auto"}, timeout=20)
run = r.json()
print("run:", run.get("run_id"), "| mode:", run.get("effective_mode"))
events_url = f"{BASE}/api/v1/chat/runs/{run['run_id']}/events"

# 5) 消费 SSE 到终止
stages, answer, sources, terminal = [], "", [], None
t0 = time.time()
with s.get(events_url, stream=True, timeout=120) as resp:
    for line in resp.iter_lines(decode_unicode=True):
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        etype = ev.get("type")
        data = ev.get("data") or {}
        if etype == "stage.changed":
            stages.append(f"{data.get('stage')}:{data.get('message','')}")
            print(f"  [{time.time()-t0:5.1f}s] stage -> {data.get('stage')} {data.get('message','')}")
        elif etype == "answer.segment":
            answer += data.get("markdown", "")
        elif etype == "answer.completed":
            terminal = data.get("terminal_reason")
            sources = data.get("sources") or []
            answer = "".join(seg.get("markdown", "") for seg in []) or answer
            break
        elif etype == "run.failed":
            terminal = "run.failed"
            print("  run.failed:", str(data)[:120])
            break
        if time.time() - t0 > 120:
            print("  超时截断"); break

print("\n=== 结果 ===")
print("stages:", stages)
print("terminal:", terminal)
print("sources:", [(s_.get("citation"), s_.get("title"), s_.get("domain")) for s_ in sources])
print("answer:", answer[:300])
