import socket, time, ssl, json, os, urllib.request
from dotenv import load_dotenv
load_dotenv(".env")
host = "api.llm.ustc.edu.cn"
print("DNS:", end=" ")
try:
    ip = socket.gethostbyname(host); print(ip)
except Exception as e:
    print("FAIL", e); raise SystemExit
print("TCP 443:", end=" ")
t0 = time.time()
try:
    s = socket.create_connection((host, 443), timeout=8); print(f"OK {time.time()-t0:.1f}s"); s.close()
except Exception as e:
    print(f"FAIL {time.time()-t0:.1f}s", e); raise SystemExit
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
key = os.getenv("LLM_API_KEY", ""); model = os.getenv("LLM_MODEL", "")
req = urllib.request.Request(base + "/chat/completions", data=json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
print("HTTPS 请求中(最长40s)...")
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read())
        print(f"LLM OK {time.time()-t0:.1f}s ->", str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))[:60])
except Exception as e:
    print(f"LLM FAIL {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:150])
