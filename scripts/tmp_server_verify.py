import urllib.request, json

# 1) 服务器 bootstrap 配置（区分环境与预算）
req = urllib.request.Request("http://114.214.241.119:8850/api/v1/system", headers={"User-Agent": "probe"})
with urllib.request.urlopen(req, timeout=15) as resp:
    cfg = json.loads(resp.read())
print("环境:", cfg.get("environment"), "| auth:", cfg.get("auth_mode"), "| 预算:", cfg.get("time_budget_seconds"))

# 2) 服务器 JS 里找新 UI 特征标记
req = urllib.request.Request("http://114.214.241.119:8850/assets/index-Ctu0HIpp.js", headers={"User-Agent": "probe"})
with urllib.request.urlopen(req, timeout=30) as resp:
    js = resp.read().decode("utf-8", "ignore")
for marker in ("科大校园智能助手", "today-strip", "stage-chip", "早上好", "source-pill"):
    print(f"JS含'{marker}':", marker in js)
print("JS大小:", len(js))
