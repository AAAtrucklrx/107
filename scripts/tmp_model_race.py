import time, urllib.request, json, os
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
key = os.getenv("LLM_API_KEY", "")
hdr = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
candidates = [
    "deepseek-v4-flash",
    "deepseek-v4-flash-ascend",
    "deepseek-v4-pro",
    "glm-5.3-flash",
    "glm-5.2-107",
    "qwen3.8-chat",
    "qwen-chat",
    "smart/default",
]
question = "用一句话介绍中国科学技术大学。"
for model in candidates:
    t0 = time.time()
    try:
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": question}],
            "max_tokens": 120,
        }).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"✅ {model}: {time.time()-t0:.1f}s -> {content[:60]!r}")
    except urllib.error.HTTPError as e:
        print(f"❌ {model}: HTTP {e.code} {time.time()-t0:.1f}s -> {e.read().decode()[:80]!r}")
    except Exception as e:
        print(f"❌ {model}: {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:60])
