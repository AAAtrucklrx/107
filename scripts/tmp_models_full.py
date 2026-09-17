import time, urllib.request, json, os
from dotenv import load_dotenv
load_dotenv(".env")
base = os.getenv("LLM_BASE_URL", "").rstrip("/")
newkey = "sk-NSsKHPVPKhvzZDyWAIXlfw"
hdr = {"Authorization": "Bearer " + newkey, "Content-Type": "application/json"}

t0 = time.time()
req = urllib.request.Request(base + "/models", headers=hdr)
with urllib.request.urlopen(req, timeout=60) as resp:
    data = json.loads(resp.read())
ids = [m["id"] for m in data.get("data", [])]
print(f"模型列表({time.time()-t0:.1f}s):", ids)
