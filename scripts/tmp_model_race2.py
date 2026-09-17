import time, urllib.request, json, os
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
key = "sk-NSsKHPVPKhvzZDyWAIXlfw"
hdr = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
candidates = ["glm-5.3-flash", "qwen3.8-chat", "smart/default", "deepseek-v4-pro", "deepseek-v4-flash"]
question = "用一句话介绍中国科学技术大学。"
for model in candidates:
    t0 = time.time()
    try:
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": question}],
            "max_tokens": 120,
        }).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read())
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"OK {model}: {time.time()-t0:.1f}s -> {content[:50]!r}")
    except urllib.error.HTTPError as e:
        print(f"ERR {model}: HTTP {e.code} {time.time()-t0:.1f}s -> {e.read().decode()[:70]!r}")
    except Exception as e:
        print(f"ERR {model}: {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:50])
