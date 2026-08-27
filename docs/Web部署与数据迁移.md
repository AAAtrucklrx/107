# 小蜗 Web 部署、数据迁移与回滚

> 适用版本：React/Vite/TypeScript + FastAPI Web 应用。产品与安全边界以 [`小蜗_Web应用与联网RAG技术规格.md`](./小蜗_Web应用与联网RAG技术规格.md) 为准。

## 1. 先选择运行模式

`XIAOWO_ENV` 与 `XIAOWO_AUTH_MODE` 是互斥、失败即停机的安全配置，不允许靠请求头自动判断环境。

| 场景 | 配置 | 可用区域 | 约束 |
|---|---|---|---|
| 本地公共开发 | `development + anonymous` | 问小蜗、校园服务 | 不保存服务端聊天历史；个人区和管理区关闭 |
| 本地合成演示 | `development + demo` | 四工作区 | 身份固定为 `PB25111691`，页面持续显示“演示数据” |
| 比赛 HTTP 演示 | `competition + demo` | 四工作区 | 只读写 demo 数据与 demo 索引，永远不能发布 production |
| 无 HTTPS 的公开服务 | `production + anonymous` | 公共能力 | 个人区和管理区同时关闭 |
| 正式个人服务 | `production + cas` | 按 CAS 身份授权 | 必须具备可信 HTTPS 来源、CAS 白名单、数据密钥和会话密钥 |

以下组合会拒绝启动：`production + demo`、HTTP CAS、anonymous 配管理员、demo 配置 `PB25111691` 以外的管理员、CAS 回调与公开来源不同源，以及启用联网时使用未批准的 sidecar 地址。

## 2. 应用部署

### 2.1 代码与依赖

GitHub 只迁移代码、Schema、来源白名单、配置模板、测试和文档。不要把 `.env`、SQLite 数据库、网页快照、批准内容或 Chroma 目录提交到 Git。

```powershell
$py = 'C:\Users\Richelieu\AppData\Local\Programs\Python\Python314\python.exe'
& $py -m pip install -r requirements.txt
Set-Location frontend
npm ci
npm test
npm run build
Set-Location ..
& $py -m pytest tests\web -q
```

`frontend/dist/` 必须由与后端同一版本的前端源码构建。FastAPI 检测到该目录后会同源托管 SPA；API 固定使用 `/api/v1`。

### 2.2 环境配置

从根目录 `.env.example` 创建服务器 `.env`，真实值只保存在服务器。常用模板：

```text
XIAOWO_ENV=competition
XIAOWO_AUTH_MODE=demo
XIAOWO_PUBLIC_ORIGIN=http://服务器实际地址:端口
XIAOWO_ADMIN_IDS=PB25111691
XIAOWO_WEB_SEARCH_ENABLED=false
XIAOWO_INGESTION_WORKER_ENABLED=false
XIAOWO_EVIDENCE_EXTRACTOR_ENABLED=true
# XIAOWO_EVIDENCE_EXTRACTOR_MODEL=默认复用 LLM_MODEL
```

正式 CAS 还必须配置：

```text
XIAOWO_ENV=production
XIAOWO_AUTH_MODE=cas
XIAOWO_PUBLIC_ORIGIN=https://审核通过的精确来源
CAS_SERVICE_URL=https://同一来源/api/v1/auth/cas/callback
XIAOWO_DATA_KEY=独立随机数据加密密钥
XIAOWO_SESSION_SECRET=至少32字节的独立随机会话密钥
```

不要在当前无 HTTPS 域名的环境启用 CAS。反向代理存在时，只信任 `XIAOWO_TRUSTED_PROXY_CIDRS` 中的明确网段；公开来源仍由 `XIAOWO_PUBLIC_ORIGIN` 唯一决定。

### 2.3 启动与健康检查

```powershell
$py = 'C:\Users\Richelieu\AppData\Local\Programs\Python\Python314\python.exe'
& $py -m uvicorn xiaowo_web.main:app --host 127.0.0.1 --port 8000
```

反向代理只暴露 FastAPI 端口，前端、API 和 SSE 保持同源。部署系统应分别探测：

- `GET /api/v1/health/live`：进程可服务。
- `GET /api/v1/health/ready`：数据库及已启用 sidecar 满足运行条件。
- `GET /api/v1/config/public`：确认运行模式和公开功能开关，不应包含密钥。

readiness 失败时不要绕过检查开放联网；liveness 可继续承载本地问答降级。

## 3. 联网 sidecar 与 worker

当前开发机不安装或启动 SearXNG、Crawl4AI 或 Docker。服务器模板位于 [`deploy/sidecars/README.md`](../deploy/sidecars/README.md)。部署时必须：

1. 只把 SearXNG 与 Crawl4AI adapter 绑定到服务器回环地址或批准的容器私网。
2. 不向 Crawl4AI 传递 CAS Cookie、浏览器 Cookie、Authorization、校内凭证或用户私密上下文。
3. 固定 Crawl4AI `v0.9.2` 的 registry digest；镜像变更后重新核验。
4. 确认 tracked Crawl4AI profile 的 LLM extraction 为 disabled，Redis 密码仅由 sidecar `.env` 注入，配置中没有模型名或明文密码 fallback。
5. 在 adapter 内运行 `verify_runtime.py`。只有版本、robots、云元数据 SSRF 阻断、peer IP 校验和公开页抓取全部通过后，才将 sidecar `.env` 的 `XIAOWO_CRAWL4AI_RUNTIME_ATTESTED` 改为 `true`。
6. 至少验证三个 SearXNG 引擎可用，并确认 `/api/v1/health/ready` 的 `evidence_extractor=true`，再启用 `XIAOWO_WEB_SEARCH_ENABLED=true`。

联网健康后，独立启动持久 worker：

```powershell
$env:XIAOWO_INGESTION_WORKER_ENABLED = 'true'
& $py -m xiaowo_web.worker
```

Web 进程和 worker 使用同一 `XIAOWO_REVIEW_DB_PATH`、`XIAOWO_WEB_EVIDENCE_DIR`、`XIAOWO_PUBLISHED_CHROMA_DIR` 与 `XIAOWO_PUBLISHED_BM25_DIR`。worker 停止只会积压任务，不应拖慢当前聊天回答。默认每 5 分钟检查一次保留策略：done 任务 7 天、dead 任务 90 天、孤儿 generation 从标记为 orphan 时起保留 7 天；审核审计和 ingestion 内容哈希墓碑长期保留。

## 4. 数据包迁移

### 4.1 数据边界

需要通过加密云盘数据包迁移的运行资产：

| 路径 | 内容 |
|---|---|
| `database/xiaowo.db` | 应用、个人演示数据、会话和聊天历史 |
| `data/course_data.db` | 评课和培养方案数据 |
| `data/review.db` | 抓取、清洗、审核、发布、generation 与审计状态 |
| `data/web_evidence/` | 不可变网页快照、批准包和 BM25 generation |
| `knowledge/chroma_db/` | 本地知识与批准 generation 向量索引 |
| `scripts/data/young_personal/` | 青春科大本地快照，仅在明确需要时迁移 |

demo 与 production 数据包分开生成、分开命名、分开恢复。密钥不进入数据包；`.env` 通过独立安全通道配置。

### 4.2 导出

1. 停止 Web 与 worker，或使用 SQLite 在线备份 API 生成一致快照；不要直接复制正在写入的数据库文件。
2. 记录 Git commit、`database/schema_web.sql` 和 `database/schema_review.sql` 的 SHA-256。
3. 对数据包内每个文件记录相对路径、字节数和 SHA-256，并写入 manifest。
4. 在 manifest 中记录 Schema 版本、导出时间、命名空间、active/previous generation ID 和应用版本。
5. 使用经过批准的加密归档工具加密后上传云盘，归档密码或密钥通过另一条通道传输。

不在 manifest 中写账号标识、CAS Ticket、Cookie、API key 或原始用户问题。

### 4.3 导入

1. 在服务器检出 manifest 记录的代码版本并完成依赖安装。
2. 保持服务停止，解密到临时目录；逐项验证路径、大小和 SHA-256。
3. 拒绝绝对路径、`..` 路径穿越、未知额外文件或命名空间不匹配的数据包。
4. 将验证通过的资产恢复到仓库相对路径，设置为仅服务账号可读写。
5. 从独立通道配置 `.env`，再启动应用和 worker。
6. 检查 live/ready、demo/CAS 身份、培养方案来源、active generation，并做一条只读检索验收。

迁移后不能把 demo 数据改名冒充 production，也不能把匿名会话合并到登录账号。

## 5. Generation 发布与回滚

审核发布会构建新的不可变 `generation_id`。Chroma 与 BM25 都写入、校验成功后，`review.db` 才在单个事务中切换 active 指针；过期、撤回和重新批准也通过下一完整 generation 生效，不在 active 索引上原地修改。同 namespace 尚未领取的发布任务会合并，未变化正文的 embedding 从持久缓存复用。

### 5.1 正常发布失败

- 条目停留在 `publish_failed` 或 `pending_publish` 时，active generation 不变。
- 修复基础设施后，在审核工作区使用“重试发布”，不要手工修改 SQLite 指针或索引文件。
- worker 用幂等键恢复任务；发布失败只影响任务关联条目。切换前留下的孤儿及超出 active/previous 窗口的旧 generation 不可检索，保留 7 天后清理。

### 5.2 回滚上一完整 generation

优先在“知识审核 → 发布治理”中查看 active、previous 与发布队列状态，再执行“回滚上一版本”。对应 API 为 `POST /api/v1/admin/generations/rollback`，必须携带当前登录会话、CSRF 和 `X-Request-ID`。

回滚只在以下条件全部满足时执行：

- 当前没有发布任务占用命名空间。
- previous generation 存在且未过期。
- manifest、BM25 文件、Chroma collection 名称、generation 元数据、文档数量和 `(document_id, content_hash)` 指纹全部通过校验。

任何完整性失败都返回 `GENERATION_INTEGRITY_INVALID`，active 指针保持不变。回滚后重新读取 `GET /api/v1/admin/generations`，并用一条只读问题确认检索结果来自目标 generation。

demo 管理员只能回滚 demo generation，永远没有 production 发布或回滚能力。production 管理必须等待 HTTPS 下的真实 CAS 管理员。

## 6. 应用版本回退

迁移切换后保留 Streamlit 一个版本作为紧急回退，不再向其增加新功能。Web 版本回退顺序：

1. 停止接收新 run，等待或取消当前 run，停止 worker。
2. 保存当前数据库一致快照和 generation manifest。
3. 回退到上一个已验证的 Git 版本，并使用该版本构建 `frontend/dist/`。
4. 只在 Schema 不兼容且已验证备份时恢复数据库；不要用旧库覆盖仍可兼容的新数据。
5. 启动后重跑 live/ready、权限、个人身份、检索与浏览器冒烟测试。

紧急使用旧界面时运行：

```powershell
& $py -m streamlit run app_test.py --server.port 8502 --server.headless true
```

旧界面只用于已知演示数据的短期回退，不能绕过新版对 HTTP production、CAS 或管理区的安全限制。

## 7. 发布前验证

除 `AGENTS.md` 的旧系统全量回归外，至少运行：

```powershell
& $py -m pytest tests\web -q
Set-Location frontend
npm test
npm run build
Set-Location ..
& $py scripts\e2e_web_workbench.py
& $py scripts\verify_web_load.py
git diff --check
```

`verify_runtime.py` 的真实探针只在服务器 sidecar 容器中执行。本地未部署 sidecar 时保持联网关闭，并明确记录该项未实测。
