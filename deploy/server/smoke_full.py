import json, urllib.request, http.cookiejar, sys
BASE="http://127.0.0.1:8000"; ORIGIN="http://114.214.241.119:8000"
jar=http.cookiejar.CookieJar(); opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
def req(path,method="GET",body=None,headers=None):
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(BASE+path,data=data,method=method)
    r.add_header("Content-Type","application/json"); r.add_header("Origin",ORIGIN)
    for k,v in (headers or {}).items(): r.add_header(k,v)
    with opener.open(r,timeout=90) as resp: return resp.status,json.loads(resp.read().decode())
st,sess=req("/api/v1/auth/session"); csrf=sess.get("csrf_token","")
st,sess=req("/api/v1/auth/demo","POST",{},{"X-CSRF-Token":csrf})
st,run=req("/api/v1/chat/runs","POST",{"question":"学生证丢了怎么办？","mode":"auto"},{"X-CSRF-Token":sess.get("csrf_token",csrf)})
run_id=run["run_id"]; print("run:", run_id, run.get("effective_mode"))
r=urllib.request.Request(BASE+f"/api/v1/chat/runs/{run_id}/events"); r.add_header("Origin",ORIGIN)
ev={}; types=[]; answers=[]
with opener.open(r,timeout=180) as resp:
    while True:
        line=resp.readline()
        if not line: break
        line=line.decode().strip()
        if line=="":
            if ev:
                t=ev.get("type"); types.append(t)
                d=ev.get("data",{})
                if isinstance(d,dict):
                    if "content" in d and d.get("content"): answers.append((t, d["content"]))
                    if "answer" in d and isinstance(d["answer"],dict) and d["answer"].get("content"): answers.append((t, d["answer"]["content"]))
                ev={}
        elif line.startswith("event: "): ev["type"]=line[7:]
        elif line.startswith("data: "):
            try: ev["data"]=json.loads(line[6:])
            except Exception: ev["data"]=line[6:]
print("event types:", types)
if answers:
    for t,c in answers: print(f"--- {t} ---"); print(c[:800])
else:
    print("NO CONTENT EVENTS FOUND")
