import time, urllib.request

for attempt in range(5):
    try:
        with urllib.request.urlopen("http://114.214.241.119:8850/api/v1/config/public", timeout=8) as resp:
            print(f"attempt {attempt+1}: {resp.status}")
            break
    except Exception as e:
        print(f"attempt {attempt+1}: FAIL {type(e).__name__}")
        time.sleep(8)
