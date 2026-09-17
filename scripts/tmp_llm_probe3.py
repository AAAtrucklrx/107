import time, urllib.request, json, os
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
key = os.getenv("LLM_API_KEY", ""); model = os.getenv("LLM_MODEL", "")
print("配置的模型:", model)
hdr = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

def probe(url, data=None, timeout=20, label=""):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            print(f"{label}: {resp.status} in {time.time()-t0:.1f}s ->", body[:220])
    except urllib.error.HTTPError as e:
        print(f"{label}: HTTP {e.code} in {time.time()-t0:.1f}s ->", e.read().decode()[:150])
    except Exception as e:
        print(f"{label}: FAIL {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:70])

probe(base + "/models", label="模型列表 GET")
probe(base + "/chat/completions", data={"model": "not-a-real-model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}, label="错误模型名")
