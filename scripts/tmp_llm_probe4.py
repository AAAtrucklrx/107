import time, urllib.request, json, os
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
newkey = "sk-NSsKHPVPKhvzZDyWAIXlfw"
hdr = {"Authorization": "Bearer " + newkey, "Content-Type": "application/json"}

def probe(label, url, data=None, key=None, timeout=20):
    t0 = time.time()
    try:
        h = {"User-Agent": "probe"}
        if key: h["Authorization"] = "Bearer " + key
        if data is not None: h["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            print(f"{label}: {resp.status} in {time.time()-t0:.1f}s ->", resp.read().decode()[:100])
    except urllib.error.HTTPError as e:
        print(f"{label}: HTTP {e.code} in {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"{label}: FAIL {time.time()-t0:.1f}s ->", type(e).__name__)

probe("无鉴权探针", base + "/chat/completions", data={"model": "x", "messages": []})
probe("新key /models", base + "/models", key=newkey)
probe("新key chat glm-5.3-flash", base + "/chat/completions", data={"model": "glm-5.3-flash", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50}, key=newkey)
