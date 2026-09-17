import time, urllib.request, json, os
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
newkey = "sk-NSsKHPVPKhvzZDyWAIXlfw"
hdr = {"Authorization": "Bearer " + newkey, "Content-Type": "application/json"}
candidates = ["glm-5.3-flash", "qwen3.8-chat", "qwen-chat", "qwen3.6-chat", "glm-chat", "deepseek-chat", "deepseek-v4-pro", "qwen3.5-non-thinking", "claude-haiku-4-5", "smart/default"]
question = "用一句话介绍中国科学技术大学。"
results = []
for model in candidates:
    t0 = time.time()
    try:
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": question}],
            "max_tokens": 150,
        }).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=75) as resp:
            data = json.loads(resp.read())
            dt = time.time() - t0
            content = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").replace("\n", " ")
            results.append((dt, model, content))
            print(f"OK {model}: {dt:.1f}s -> {content[:56]!r}")
    except urllib.error.HTTPError as e:
        print(f"ERR {model}: HTTP {e.code} {time.time()-t0:.1f}s -> {e.read().decode()[:60]!r}")
    except Exception as e:
        print(f"ERR {model}: {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:50])
print()
if results:
    results.sort()
    print("按延迟排序:", [f"{m}({d:.1f}s)" for d, m, _ in results])
