# 小蜗联网 Sidecar 部署模板

本目录只保存配置模板，不会在开发机安装或启动 Docker、SearXNG 或 Crawl4AI。

## 边界

- SearXNG 和小蜗 Crawl4AI adapter 只绑定服务器回环地址。
- Crawl4AI 上游不发布宿主机端口，不接收浏览器、CAS 或校内凭证。
- adapter 固定核验 Crawl4AI `v0.9.2`，并只发送由模板生成的安全配置。
- `XIAOWO_CRAWL4AI_RUNTIME_ATTESTED` 默认是 `false`。未完成运行探针时 adapter 健康状态为 `degraded`，小蜗 readiness 不会放行联网。
- 正式部署应把镜像标签解析并固定为相同版本的 registry digest；变更镜像后重新探针。

## 服务器部署

1. 将 `.env.example` 复制为本目录 `.env`，分别生成三个独立随机密钥。
2. 检查 `compose.yml` 中镜像版本和回环端口；不要把 `8080`、`11235` 改成公网绑定。
3. 在服务器运行 `docker compose --env-file .env -f compose.yml config`，先检查展开后的配置。
4. 启动 sidecar 后，在 adapter 容器内运行：

```powershell
docker compose --env-file .env -f compose.yml run --rm crawl4ai-adapter python /app/verify_runtime.py
```

5. 只有版本检查、云元数据 SSRF 阻断和公开页面抓取都通过后，将 `.env` 中 `XIAOWO_CRAWL4AI_RUNTIME_ATTESTED=true` 并重启 adapter。
6. 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/search?q=USTC&format=json
Invoke-RestMethod http://127.0.0.1:11235/health
```

adapter 健康响应必须同时包含 `egress_protection=true`、`robots=true`、`allow_internal_urls=false`、`peer_ip_verification=true` 和 `upstream_version=0.9.2`。随后才可在小蜗 `.env` 中启用：

```text
XIAOWO_WEB_SEARCH_ENABLED=true
XIAOWO_SEARXNG_URL=http://127.0.0.1:8080
XIAOWO_CRAWL4AI_URL=http://127.0.0.1:11235
```

如任一健康项失败，保持联网关闭；本地问答仍可继续运行。
