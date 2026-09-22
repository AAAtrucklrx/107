"""通过 Docker socket 在容器内执行命令（curl 替代品：httpx UDS）"""
import sys, time, json, httpx

def client():
    return httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock"), base_url="http://localhost", timeout=120)

cli = client()
containers = cli.get("/containers/json").json()
names = {c["Names"][0]: c["Id"] for c in containers}
print("容器:", {n: i[:12] for n, i in names.items()})

want = sys.argv[1]
cid = next((i for n, i in names.items() if want in n), None)
if not cid:
    print("未找到容器：", want); sys.exit(1)
cmd = sys.argv[2:]
r = cli.post(f"/containers/{cid}/exec", json={
    "AttachStdout": True, "AttachStderr": True, "Tty": True,
    "Cmd": cmd,
})
eid = r.json()["Id"]
resp = cli.post(f"/exec/{eid}/start", json={"Detach": False, "Tty": True})
print(resp.text)
for _ in range(60):
    time.sleep(1)
    info = cli.get(f"/exec/{eid}/json").json()
    if info.get("Running") is False:
        print("EXIT:", info.get("ExitCode"))
        break
