# 小蜗联网 Sidecar 部署教程（宿主机逐步操作 · 2026-09-01）

> 适用：Ubuntu 24.04 云主机（192.167.103.3 / 114.214.241.119），仓库在 `/root/Desktop/小蜗`。
> 你在**宿主机终端**（非 DSH 沙箱）执行以下步骤；每步都有预期结果。全部完成后把 Step 9 的清单发给 AI，AI 负责查验与后续启用。
> 已由 AI 完成的准备工作：`deploy/sidecars/searxng/settings.yml` 引擎已换成本机可达组合（baidu/bing/sogou/360search/mojeek——原 brave/duckduckgo/wikipedia 本机不可达）。

---

## Step 0 · 前置确认

```bash
whoami                      # 应为 root（或你有 sudo）
ls /root/Desktop/小蜗/deploy/sidecars
```

预期：目录里有 `compose.yml`、`searxng/`、`crawl4ai/`、`crawl4ai_adapter/`、`.env.example`、`README.md`、`DEPLOY-STEP-BY-STEP.md`（本文件）。

## Step 1 · 安装 Docker（官方源，本机已实测可达）

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

验证（两条都应有版本号输出）：
```bash
sudo docker version
sudo docker compose version
```

## Step 2 · 配置镜像加速（本机 Docker Hub 直连不通，已实测）

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://hub.rat.dev",
    "https://registry.cn-hangzhou.aliyuncs.com"
  ]
}
EOF
sudo systemctl restart docker
```

验证拉取能走通镜像（首次会下载，稍等）：
```bash
sudo docker pull hello-world
sudo docker run --rm hello-world
```
预期：输出 "Hello from Docker!"。若拉取失败/超时：告诉我，备选方案是换加速源或用 Watt Toolkit 的 Docker Hub 加速。

## Step 3 · 生成 sidecar 密钥

```bash
cd /root/Desktop/小蜗/deploy/sidecars
cp .env.example .env
openssl rand -base64 32     # 生成第 1 个，填给 CRAWL4AI_API_TOKEN
openssl rand -base64 32     # 第 2 个 → CRAWL4AI_REDIS_PASSWORD
openssl rand -base64 32     # 第 3 个 → SEARXNG_SECRET
nano .env                   #（或用 vi）把三个值填进去；其余保持默认
```

填好后 `cat .env` 检查：三个值都不是 `replace-with-...`；`XIAOWO_CRAWL4AI_RUNTIME_ATTESTED=false`（先保持 false，Step 7 后才改）。

## Step 4 · 校验配置并启动

```bash
cd /root/Desktop/小蜗/deploy/sidecars
sudo docker compose --env-file .env -f compose.yml config     # 无报错、展开正常即通过
sudo docker compose --env-file .env -f compose.yml build      # 构建 adapter（首次拉 python:3.13-slim）
sudo docker compose --env-file .env -f compose.yml up -d
```

等待 2~5 分钟（crawl4ai 镜像较大含浏览器），查看状态：
```bash
sudo docker compose ps
```
预期：`searxng`、`crawl4ai-upstream`、`crawl4ai-adapter` 三个服务 `Up` 且 `healthy`（刚启动初期 adapter 可能 pending，等 upstream healthy 后自动起来）。

看日志确认无报错：
```bash
sudo docker compose logs --tail=50 searxng
sudo docker compose logs --tail=50 crawl4ai-adapter
```

## Step 5 · 搜索引擎自检（关键）

```bash
# 基本信息查询（JSON API）
curl -s 'http://127.0.0.1:8080/search?q=%E4%B8%AD%E5%9B%BD%E7%A7%91%E5%AD%A6%E6%8A%80%E6%9C%AF%E5%A4%A7%E5%AD%A6&format=json' | python3 -c "import json,sys; d=json.load(sys.stdin); print('results:', len(d.get('results', [])), '| engines:', sorted({r.get('engine') for r in d.get('results',[])}))"

# 逐引擎验证（5 个都跑一遍）
for e in baidu bing sogou 360search mojeek; do
  n=$(curl -s "http://127.0.0.1:8080/search?q=%E4%B8%AD%E5%9B%BD%E7%A7%91%E5%AD%A6%E6%8A%80%E6%9C%AF%E5%A4%A7%E5%AD%A6&format=json&engines=$e" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('results',[])))")
  echo "$e -> $n 条"
done
```
预期：至少 3 个引擎 ≥1 条结果（baidu/bing/sogou/360search/mojeek 均已实测可达，一般问题不大）。若某引擎 0 条或报错：把对应输出发我，我来调（如加参数/换引擎组合）。

## Step 6 · adapter 健康检查（attestation 前是 degraded 属正常）

```bash
curl -s http://127.0.0.1:11235/health | python3 -m json.tool
```
预期：包含 `robots=true`、`egress_protection=true`、`allow_internal_urls=false`、`peer_ip_verification=true`、`llm_disabled=true`、`upstream_version=0.9.2`；`runtime_attested` 为 false（正常，下一步才核验）。

## Step 7 · 运行 attestation 探针

```bash
cd /root/Desktop/小蜗/deploy/sidecars
sudo docker compose --env-file .env -f compose.yml run --rm crawl4ai-adapter python /app/verify_runtime.py
```
预期：逐项打印并通过（版本检查 / robots 生效 / 云元数据 SSRF 阻断 / peer IP 校验 / 公开页面抓取），最后类似 `ATTESTATION OK`。任一失败：把输出发我，勿继续。

## Step 8 · 通过后启用 attestation

```bash
cd /root/Desktop/小蜗/deploy/sidecars
sed -i 's/^XIAOWO_CRAWL4AI_RUNTIME_ATTESTED=false/XIAOWO_CRAWL4AI_RUNTIME_ATTESTED=true/' .env
sudo docker compose --env-file .env -f compose.yml up -d crawl4ai-adapter
sleep 5
curl -s http://127.0.0.1:11235/health | python3 -m json.tool
```
预期：`runtime_attested=true` 且 `status=ok`（不再是 degraded）。

## Step 9 · 回报清单（把以下逐项原样发给 AI）

1. `sudo docker compose ps` 的完整输出；
2. Step 5 逐引擎结果（5 行）；
3. `curl -s http://127.0.0.1:11235/health` 的输出；
4. Step 7 `verify_runtime.py` 的完整输出（或"CVE 通过"摘要）；
5. （可选）`sudo docker compose logs --tail=20 searxng`。

收到后 AI 会：查验 sidecar 契约 → 小蜗 `.env` 打开 `XIAOWO_WEB_SEARCH_ENABLED=true` → 重启 Web → 跑 readiness/联网问答冒烟 → 必要时调优（预算/引擎/并发）并回归，最后汇报。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| `docker compose config` 报 `${...:?set ...}` | `.env` 没填好，回到 Step 3 |
| 拉镜像 429 / 超时 | 换加速源顺序或改 1ms.run 或 rat.dev；再不行用 Watt Toolkit 加速（Steam++ 支持 Docker Hub） |
| searxng 起来后 curl 返回 403 | 把 `formats: [html, json]` 保留（模板已显式配置），确认访问的是 JSON API 路径 |
| adapter degraded 且显示 `upstream_version` 不是 0.9.2 | 镜像被替换了：`CRAWL4AI_IMAGE` 固定回 `unclecode/crawl4ai:0.9.2` 并重启 |
| crawl4ai-upstream 反复重启 | `docker compose logs crawl4ai-upstream` 看了发我（多半是 redis 密码/内存限制） |
| 想固定镜像 digest（推荐生产） | `sudo docker image inspect --format '{{index .RepoDigests 0}}' unclecode/crawl4ai:0.9.2` 把结果填入 .env 的 `CRAWL4AI_IMAGE=unclecode/crawl4ai@sha256:...` 再 `up -d`（digest 变了须重跑 Step 7） |
