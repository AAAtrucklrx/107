import urllib.request, json

req = urllib.request.Request("http://114.214.241.119:8850/api/v1/config/public", headers={"User-Agent": "probe"})
with urllib.request.urlopen(req, timeout=15) as resp:
    cfg = json.loads(resp.read())
print("环境:", cfg.get("environment"), "| auth:", cfg.get("auth_mode"), "| 预算:", cfg.get("time_budget_seconds"))

req = urllib.request.Request("http://114.214.241.119:8850/assets/index-Ctu0HIpp.js", headers={"User-Agent": "probe"})
with urllib.request.urlopen(req, timeout=30) as resp:
    js = resp.read().decode("utf-8", "ignore")
for marker in ("科大校园智能助手", "today-strip", "stage-chip", "早上好", "source-pill", "chat-workspace--solo"):
    print(f"JS含'{marker}':", marker in js)
print("JS大小:", len(js))
