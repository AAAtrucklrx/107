import time, urllib.request, json, os
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")

def probe(url, data=None, headers=None, timeout=20, label=""):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            print(f"{label}: {resp.status} in {time.time()-t0:.1f}s")
    except urllib.error.HTTPError as e:
        print(f"{label}: HTTP {e.code} in {time.time()-t0:.1f}s (服务有响应)")
    except Exception as e:
        print(f"{label}: FAIL {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:70])

probe(base + "/", timeout=15, label="LLM根路径 GET")
probe(base + "/chat/completions", data=json.dumps({"model": "x", "messages": []}).encode(), headers={"Content-Type": "application/json"}, timeout=15, label="chat接口(无鉴权)")
