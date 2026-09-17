import os, urllib.request, time
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
key = os.getenv("LLM_API_KEY", "")
model = os.getenv("LLM_MODEL", "")
url = base + "/chat/completions"
body = None
import json
req = urllib.request.Request(url, data=json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        print(f"LLM OK in {time.time()-t0:.1f}s ->", str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))[:50])
except Exception as e:
    print(f"LLM FAIL in {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:120])
