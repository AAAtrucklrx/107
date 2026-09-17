import time, urllib.request

for host in ("catalog.ustc.edu.cn", "www.ustc.edu.cn"):
    t0 = time.time()
    try:
        req = urllib.request.Request(f"https://{host}/", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"{host}: OK {resp.status} in {time.time()-t0:.1f}s, {len(resp.read())} bytes")
    except Exception as e:
        print(f"{host}: FAIL {time.time()-t0:.1f}s ->", type(e).__name__, str(e)[:80])
