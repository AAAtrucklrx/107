import time, urllib.request
t0 = time.time()
try:
    req = urllib.request.Request("http://114.214.241.119:8850/", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", "ignore")
        import re
        m = re.search(r'assets/index-([A-Za-z0-9_-]+)\.js', body)
        print(f"公网部署: {resp.status} in {time.time()-t0:.1f}s, JS入口 index-{m.group(1) if m else '?'}")
        print("本地新构建 JS: index-D5tuVwax 换算后应为最新 hash（见 frontend/dist/assets）")
except Exception as e:
    print(f"公网部署: FAIL {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:80])
