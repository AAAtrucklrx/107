# 小蜗 Web 应用与联网 RAG 技术规格

> 状态：Accepted（产品决策 Q1-Q80 已确认）
> 日期：2026-08-27
> 适用范围：React/FastAPI 重构、联网证据保底、异步知识审核、演示身份与迁移验收
> 架构图：[`小蜗_Web应用与联网RAG目标架构.drawio`](./小蜗_Web应用与联网RAG目标架构.drawio)

## 1. 目标

将现有 Streamlit 应用迁移为可长期维护的“科大学术工作台”，同时保留现有 LangGraph、工具、课程库、培养方案和多用户隔离语义。

新版必须实现：

1. React + Vite + TypeScript 前端与 FastAPI 模块化单体后端。
2. 本地知识不足时，以 SearXNG 搜索、Crawl4AI 抽取的公开互联网证据作为保底。
3. 在证据达到确定性门槛后才输出回答正文；证据不足或冲突时如实说明。
4. 回答结束后异步生成知识审核草稿，不阻塞当前用户回答。
5. 公开问答允许匿名使用；真实个人能力只允许 CAS 身份。当前没有 HTTPS 域名，因此首期仅实现真实认证接口，测试和比赛使用明确标识的演示模式。
6. 用一个唯一的合成演示身份 `PB25111691` 验证个人页面和数据隔离。
7. 在新旧功能对照、回归、安全、桌面和移动 E2E 全部通过后替换 Streamlit。

## 2. 非目标

首期不实现：

- 用户文件上传、扫描 PDF OCR、图片理解、视频或音频解析。
- 把全部互联网内容永久收录，或让模型自动批准知识。
- 展示模型思维链、工具规划理由或内部提示词。
- 浏览器直连 SearXNG、Crawl4AI、CAS Cookie 或校内凭证。
- Redis、Celery、WebSocket、微服务拆分、Next.js、SSR 或 SEO 页面。
- 在当前开发机安装 SearXNG、Crawl4AI、Docker、WSL2、draw.io 或 Graphviz。
- 长期维护 React 与 Streamlit 两套生产 UI。

## 3. 不可回退的产品边界

### 3.1 推荐与个人身份

- 课程范围是推荐硬条件；兴趣、工作量、教师和目标学期默认只参与软排序，只有“只要/必须”升级为硬过滤。
- “推荐且不冲突”只执行推荐并说明没有检查课表；独立 `check_course_conflict` 继续保留。
- 不得重新引入推荐侧 `schedule_constraints`、`force_calls`、`pending_force_calls` 或培养方案三按钮。
- 已登录专业和年级只来自当前用户已验证档案。取不到时不猜、不继承匿名选择。
- 个人方案失败时可显示已验证专业的通用方案，但必须标记“专业通用参考，不是个人培养方案”。

### 3.2 联网结论

联网门控满足任一条件即触发：

- 本地检索 `found=false`。
- 本地证据没有达到当前问题所需的权威门槛。
- 问题明确要求“最新、今天、当前”等时效信息。
- 用户本轮选择“联网”。

用户选择“本地”时禁止联网。默认模式为“自动”，模式仅作用于当前一轮。

确定结论必须满足以下任一条件：

- 一个直接、有效、在审核白名单中的权威一手来源。
- 两个相互独立、结论一致的可靠来源。

转载、镜像、相同正文或共同引用同一上游内容不能算独立来源。冲突时单列“信息存在分歧”，不得由模型猜测。未达门槛时回答“暂未找到足够可靠的联网证据”，并列出已找到但不足以确认的来源。

## 4. 目标架构

### 4.1 运行容器

| 容器 | 职责 | 部署边界 |
|---|---|---|
| React SPA | 四工作区、匿名历史、SSE 消费、主题与交互 | 浏览器，同源 `/` |
| FastAPI | 认证、会话、版本化 API、SSE、学业门面、证据编排、审核 API | 单个 Python 应用，公开 `/api/v1` |
| 现有小蜗核心 | LangGraph QA、工具注册表、课程/方案/日程服务、Chroma/BM25 本地召回 | FastAPI 进程内复用，通过适配器隔离 |
| Ingestion worker | 清洗、分类、去重、生成审核草稿、过期扫描和发布任务 | 独立 Python 进程 |
| SearXNG | 聚合搜索，启用 JSON API | 仅回环或容器私网 |
| Crawl4AI | robots-aware HTML 抽取 | 独立 sidecar，不持有任何校内凭证 |
| 数据存储 | 应用库、审核库、课程库、原始快照、批准索引 | 全部位于仓库相对的 gitignore 数据目录 |

FastAPI 是模块化单体，不把认证、聊天、学业或审核拆成网络微服务。同步 `run_qa` 初期通过有界线程执行器接入；后续在保持 API 事件契约不变的前提下改造内部流式节点。

### 4.2 建议目录

```text
frontend/                 React/Vite/TypeScript
xiaowo_web/
  api/                    路由、请求/响应 Schema
  auth/                   AuthProvider、会话与权限
  chat/                   运行管理、SSE 事件、历史
  evidence/               门控、搜索、抓取、可信度和引用
  academic/               现有个人/学业服务门面
  review/                 队列、版本、审核、发布
  storage/                SQLite 仓储、迁移和加密字段
  worker/                 独立 worker 入口与任务处理器
  main.py                 FastAPI 入口
deploy/                   SearXNG/Crawl4AI 配置模板与运行说明
tests/web/                 API、契约、安全和集成测试
```

## 5. 运行模式与认证

`XIAOWO_AUTH_MODE` 只能取 `anonymous`、`demo`、`cas`，模式互斥。`XIAOWO_ENV` 只能取 `development`、`competition`、`production`；生产环境默认且安全值为 `anonymous`。

### 5.1 Anonymous

- 允许公共本地问答和联网搜索。
- 匿名历史只写浏览器 IndexedDB，保存 30 天并可一键清除。
- 个人区和管理区不渲染，后端相应接口仍执行权限拒绝。

### 5.2 Demo

- 只使用 `PB25111691` 一个合成身份及仓库现有演示数据。
- 登录入口不接收用户名或密码，直接创建该演示会话。
- 页面始终显示“演示数据”，支持恢复初始状态。
- `XIAOWO_ADMIN_IDS` 包含该身份时才显示审核区。
- 只允许开发、比赛或服务器本机启用；不得与 CAS 同时启用。
- demo 可在比赛 HTTP 环境展示合成个人数据，但不能访问真实 CAS 数据。demo 管理员默认只操作 demo 审核命名空间，不能发布到生产知识索引。

### 5.3 CAS

- 浏览器只持有随机、不透明的 `HttpOnly + Secure + SameSite=Lax` 会话 Cookie。
- 服务端只保存会话令牌哈希；登录后轮换，闲置 12 小时、绝对期限 7 天，登出立即失效。
- CAS Ticket 仅在服务端回调中验证一次，不写日志、数据库或浏览器存储。
- 个人资料按认证学号绑定；返回学号不一致时拒绝使用。
- 正式 CAS、个人区和管理区必须在可信 HTTPS 来源下启用。当前 HTTP 部署只允许 anonymous；代码和测试保留 CAS Provider 接口。
- 非 `cas` 模式下 CAS 登录/回调端点固定返回 `AUTH_MODE_DISABLED`，不能因为接口代码存在而尝试真实认证。

所有有副作用的同源请求执行 Origin/CSRF 校验。管理员权限最终只来自服务端 `XIAOWO_ADMIN_IDS`，前端隐藏不构成授权。

## 6. 聊天与 SSE 契约

### 6.1 请求生命周期

1. `POST /api/v1/chat/runs` 校验会话、内容长度和联网模式，返回 `run_id`。
2. `GET /api/v1/chat/runs/{run_id}/events` 以 SSE 返回可重连事件。
3. 客户端可调用 `POST /api/v1/chat/runs/{run_id}/cancel`。
4. demo/CAS 回答完成后先提交 90 天历史；anonymous 只返回并保留最多 1 小时的 run 事件，不写服务端对话历史。随后以非阻塞方式写入合格的公开证据反哺任务。

事件只允许以下公开类别：

| 事件 | 内容 |
|---|---|
| `run.created` | 运行 ID、模式和时间预算 |
| `stage.changed` | `local_retrieval`、`web_search`、`evidence_check`、`answering` 等固定枚举和简短文案 |
| `source.found` | 可公开的来源元数据，不含抓取内部信息 |
| `answer.segment` | 完整句子或完整结构块，不发送半句 |
| `answer.completed` | 最终引用、限制和结束原因 |
| `run.failed` | 稳定错误码及可操作提示，不返回堆栈 |

禁止通过事件发送 `thought_log`、模型 reasoning、内部工具选择理由、提示词或凭证。SSE 事件至少保留 1 小时以支持 `Last-Event-ID` 续传。

### 6.2 时间预算

| 阶段 | 上限 |
|---|---:|
| SearXNG | 4 秒 |
| Top 3 并行抓取 | 最多 8 秒 |
| 证据阶段总计 | 12 秒 |
| 模型回答 | 到第 18 秒 |
| 确定性收尾 | 最后 2 秒 |
| 整次回答 | 20 秒硬上限 |

达到证据门槛即取消仍在等待的低优先级抓取。第 18 秒前模型未形成完整回答时，切换为基于结构化证据的简洁模板；不得留下半句或未经核验的结论。

## 7. 联网证据管线

### 7.1 查询安全

任何文本发送给搜索引擎或搜索改写模型前，必须经过确定性隐私清洗。永久禁止外发：学号、姓名、成绩、课表、培养方案、用户画像、CAS Cookie、Ticket、校内凭证和完整私密对话历史。

模型只能接触清洗后的当前问题与必要的公开上下文。无法可靠脱敏时拒绝联网并说明原因。

### 7.2 搜索

- SearXNG 显式启用 `search.formats: [html, json]`，API 仅对 FastAPI 可见。
- 处理 `unresponsive_engines`，区分真正零结果与部分引擎故障。
- 初始引擎配置包含百度、Bing 中国、DuckDuckGo、Brave 和 Wikipedia；部署时以健康检查结果启用至少三个可用引擎。
- 科大问题并行运行“审核官方白名单通道”和“全网通道”。`site:` 只是搜索提示，结果仍须后置过滤和重排。
- 搜索结果缓存 5 分钟；“最新/今天/当前”仍必须重新验证。

### 7.3 抓取与 SSRF 防护

- 仅允许公开 HTTP/HTTPS。
- URL 解析、DNS 解析以及每次重定向后都重新阻断回环、私网、link-local、保留地址和云元数据目标。
- 禁止向 Crawl4AI 传递 CAS Cookie、浏览器 Cookie、Authorization 或任何校内凭证。
- 显式启用 robots 检查，按主机限速，并设置响应体、重定向次数和下载时间上限。
- HTML 正文上限 2 MB；PDF 上限 20 MB、200 页，只处理可直接提取文本的 PDF。
- 图片只保留引用；扫描件、加密、超限或无法解析的文档只记录失败原因，不进入审核库。
- 页面正文视为不可信数据，不能改变系统指令、触发工具调用或授权操作。

### 7.4 来源等级

| 等级 | 含义 | 结论能力 |
|---|---|---|
| `official_primary` | 审核白名单中的直接官方一手栏目 | 单一有效来源可确认 |
| `reliable_independent` | 可验证机构、出版物或稳定专业来源 | 两个独立且一致才可确认 |
| `general` | 一般网页、聚合或背景材料 | 只能辅助，不可单独确认 |
| `unverified` | 未知、缺失元数据或存在异常 | 只能作为“证据不足”来源展示 |

未知 `*.ustc.edu.cn` 只附加 `ustc_domain` 标签，不自动成为 `official_primary`。正式白名单保存在 Git 跟踪的 `config/source_trust.yaml`；管理页面只能提交变更建议，不能即时抬高来源等级。

### 7.5 缓存与时效

网页使用 ETag、Last-Modified 和内容哈希条件复用。缓存内容不得延长批准知识的有效期，也不得作为“当前”结论的唯一证据。服务不可用时可展示明确标记的历史缓存，但最新问题必须返回证据不足。

## 8. 异步反哺与审核

### 8.1 入队条件和内容

只有实际支撑过回答、通过安全/质量/近重复检查、且属于科大校园或高复用通识的新版本可以进入审核队列。

队列只保存：

- 不可变公开网页/PDF 快照及哈希。
- 实际引用片段和证据关系。
- 脱敏主题、来源元数据和抓取结果。
- 回答运行的非用户可识别关联 ID。

不保存用户原始问题、账号标识、个人资料、私密上下文或完整回答。

### 8.2 Worker

首期使用 SQLite 持久队列和独立 Python worker，不引入 Redis/Celery。任务必须支持原子领取、租约、幂等键、指数退避、最大重试和死信状态。聊天服务故障或 worker 停止时，任务持久保留且不阻塞回答。

清洗模型通过独立适配器配置，默认复用科大 LLM。它只能去噪、提取原子事实、生成标题/关键词/分类和分块草稿；禁止补充事实、自动认定官方、合并冲突或自动批准。

### 8.3 审核与版本

审核者必须能查看原文、模型清洗稿、差异和分块，可编辑并逐块批准。禁止批量批准，只允许批量标记重复或拒绝。

原始快照、模型稿、人工稿和批准分块分别版本化且不可覆盖。编辑创建新版本；撤回或过期只改变有效状态，历史继续保留。

批准内容通过可恢复发布任务写入 Chroma 和 BM25。两套索引都成功后才切换为 active；部分失败保持 pending_publish，可重试或回滚。

### 8.4 默认有效期

| 分类 | 有效期 |
|---|---:|
| 公告 | 7 天 |
| 动态办事信息 | 30 天 |
| 政策制度 | 90 天 |
| 稳定通识 | 180 天 |

有效期按批准分块计算。模型提出分类，审核者确认；审核者可缩短，超过分类上限的延长必须重新核验。过期分块退出有效检索但保留审核历史；重新抓取后即使正文未变也进入复核。

## 9. 数据边界

### 9.1 存储

| 位置 | 内容 |
|---|---|
| `database/xiaowo.db` | 应用与学业数据、服务端会话、登录历史、90 天聊天历史、偏好和反馈引用 |
| `data/review.db` | 抓取任务、来源、快照元数据、清洗版本、分块、审核、发布 outbox 和审计记录 |
| `data/course_data.db` | 现有评课/培养方案数据，只读访问 |
| `data/web_evidence/raw/` | 按 SHA-256 寻址的不可变原始快照 |
| `data/web_evidence/approved/` | 批准版本的可迁移内容包 |
| `knowledge/chroma_db/` | 有效向量索引；BM25 从批准版本构建 |

Schema 和迁移脚本进入 Git；数据库、原始网页、审核库、批准数据和 Chroma 全部 gitignore。云盘数据包必须加密，并包含 Schema 版本、文件清单和 SHA-256 校验值。密钥单独配置。

### 9.2 个人数据

专业、年级、成绩、课表和个人方案按认证学号严格隔离，敏感缓存字段使用 `XIAOWO_DATA_KEY` 进行带认证加密。默认缓存 24 小时；超期数据只能标记为“上次同步”，不得冒充实时。

登录历史服务端保存 90 天，可删除单条或全部。删除立即移除活动数据库正文，只保留无内容的删除审计事件；加密备份最多 30 天轮换清除。匿名记录不上传，也不在登录后合并。

回答反馈保存 30 天并进入独立反馈队列，不能直接进入知识审核队列。

## 10. API 表面

所有 API 使用 `/api/v1`，以 OpenAPI Schema 作为前后端契约。

| 模块 | 主要端点 |
|---|---|
| 系统 | `GET /health/live`、`GET /health/ready`、`GET /config/public` |
| 认证 | `GET /auth/session`、`POST /auth/demo`、`GET /auth/cas/login`、`GET /auth/cas/callback`、`POST /auth/logout` |
| 会话 | `GET/POST /conversations`、`DELETE /conversations/{id}`、`DELETE /conversations` |
| 回答 | `POST /chat/runs`、`GET /chat/runs/{id}/events`、`POST /chat/runs/{id}/cancel` |
| 学业 | `GET /academic/overview`、`/program`、`/courses`、`/schedule` |
| 校园 | `GET /campus/services`、`GET /campus/activities` |
| 反馈 | `POST /answers/{id}/feedback` |
| 管理 | `GET /admin/review-items`、版本/分块详情、编辑、逐块批准、拒绝、撤回、复抓、反馈列表 |

个人和管理端点必须同时执行认证模式、会话身份和权限检查。所有列表使用稳定游标分页；错误响应使用稳定代码，不暴露异常文本。

## 11. 前端体验

### 11.1 信息架构

- **问小蜗**：默认首屏。桌面显示会话历史栏和主对话区；移动端历史进入抽屉。
- **我的学业**：总览、培养方案、课程、课表标签页。数据来自统一后端，每项最多一个“问问小蜗”上下文动作。
- **校园服务**：统一搜索、审核官方入口和活动信息。
- **知识审核**：只对管理员渲染，采用队列与详情双栏；移动端改为列表到详情的分步导航。

桌面使用窄左侧导航，移动端使用三个用户工作区的底部导航。管理员入口位于账户菜单。

### 11.2 对话交互

- 输入框旁使用“自动 / 联网 / 本地”三态分段控制。
- 联网时展示固定阶段状态，不展示思维链。
- 正文使用 `[1]` 行内引用；末尾来源区显示标题、机构/域名、发布时间、抓取时间、等级和失效状态。
- 冲突信息单独显示。证据不足来源仍可查看，但不能伪装成确认结论。
- 每条回答提供停止、复制、按原模式重试、强制联网重试和反馈。重试生成独立版本并保留原回答。

### 11.3 视觉系统

方向是安静、精确的“科大学术工作台”，首屏直接进入应用，不做营销 Hero，不使用官方校徽，也不采用通用后台模板。

基础色：

- 主操作蓝 `#034EA1`。
- 联网/检索青 `#099BA8`。
- 浅色背景 `#EEF3F7`，配白色工作面和中性灰边界。
- 深色使用近黑中性背景、灰色工作面，并重新校准蓝、青和状态色对比度，不做整页深蓝。
- 成功、警告、错误使用独立绿、黄、红状态色。

使用 CSS 变量、Tailwind CSS、Radix 无样式交互原语和 Lucide 图标。卡片只用于重复项、弹窗和需要明确框定的工具；不嵌套卡片，不把页面区块全部浮成卡片。

定制仓库内 SVG“小蜗”中文字标，配克制几何旋纹，不使用卡通吉祥物。首次访问为浅色；主题保存在浏览器，登录后可同步偏好。动效仅用于状态和页面过渡，并尊重 `prefers-reduced-motion`。

最低验收为 360px 至宽屏、纯键盘操作、清晰焦点、WCAG 2.2 AA 对比度，以及主流浏览器最近两个版本。UI 首期为中文，回答语言跟随问题，代码结构预留国际化。

## 12. 故障与降级

- SearXNG/Crawl4AI 不可用：本地问答继续；最新问题返回证据不足。
- Worker 不可用：回答继续，反哺任务留在持久队列。
- LLM 不可用：沿用现有确定性工具/知识摘要降级，不编造数据。
- Chroma 维度或服务异常：沿用 BM25 降级并标记来源。
- CAS 不可用或当前无 HTTPS：匿名能力继续，个人区和管理区关闭。
- 某个搜索引擎异常：展示系统降级状态，不能把引擎故障当作零结果。

所有降级都必须标记数据来源和限制，不得返回伪造的空教室、成绩、冲突、URL 或“最新”结论。

## 13. 实施阶段

1. **规范与契约**：落地本规格、架构图、API Schema、运行模式和安全配置模板。
2. **后端纵向切片**：FastAPI 健康检查、demo 会话、`run_qa` 适配、聊天 run 与 SSE 模拟/真实事件。
3. **前端外壳**：四工作区、主题、字标、响应式导航、对话与来源组件，先对接 mock contract。
4. **本地 QA 接入**：复用现有工具卡片和个人学业门面，保持推荐/身份边界。
5. **联网证据**：SearXNG/Crawl4AI 客户端、SSRF、robots、PDF、可信度、引用、缓存和硬时限。
6. **反哺审核**：SQLite worker、不可变版本、差异/分块审核、原子发布、过期复核和反馈队列。
7. **迁移验收**：功能对照、全量测试、桌面/移动 E2E、并发与故障演练；切换后保留 Streamlit 一个版本再移除。

阶段间通过 API 契约隔离，不能为赶进度绕过证据门槛、安全检查或个人身份绑定。

## 14. 验收与测试

### 14.1 必须保留

AGENTS.md 所列现有回归全部继续通过。任何新版适配不得改变已确认的推荐语义、独立冲突检查、个人培养方案隔离和降级来源标记。

### 14.2 新增测试

- Python 单元测试：认证模式、会话、历史删除、证据门控、独立来源、TTL、去重、发布状态机。
- API 契约测试：OpenAPI、错误码、权限、游标、SSE 事件顺序、断线续传和取消。
- 安全测试：SSRF、DNS rebinding/重定向复检、Cookie、CSRF、日志脱敏、Prompt Injection、超限 HTML/PDF。
- Worker 测试：租约竞争、幂等、崩溃恢复、重试、死信、部分索引失败和回滚。
- 前端测试：主题、导航、三态联网控制、来源、冲突、证据不足、键盘和 reduced motion。
- E2E：浅/深色，1440×1000 与 390×844，匿名/演示/管理员，个人方案来源标记，无横向溢出和元素遮挡。
- 基础负载：100 个在线 SSE 连接、30 个并发回答；超载时有界排队并返回明确繁忙提示。

真实 sidecar 集成测试必须由环境变量显式启用；默认本地测试使用契约 mock，不安装外部服务。涉及外部 LLM 和个人数据的测试继续遵守现有授权边界。

### 14.3 Streamlit 切换条件

只有同时满足以下条件才切换：

- 旧回归全部通过。
- 新增 API、身份隔离、SSE、安全、审核发布和 E2E 全部通过。
- 四工作区功能对照表无阻断缺口。
- demo 模式在 `PB25111691` 上可重复恢复并明确标识。
- anonymous 环境无法访问个人或管理接口。
- sidecar、LLM、worker、Chroma 任一故障均通过降级演练。
- 部署、数据包、回滚和密钥说明已更新。

### 14.4 本地迁移验收记录（2026-08-27）

| 旧能力/新增目标 | Web 落点 | 本地验收 |
|---|---|---|
| 本地 RAG、统一 QA、28 内置工具与生态工具 | “问小蜗” + `/chat/runs` SSE；`LegacyQaRunner` 复用现有 LangGraph | 工具/节点旧回归保留；SSE 只输出公开阶段、完整句段、来源与限制 |
| 成绩、GPA、课程、课表、培养方案 | “我的学业”四标签 + `/academic/*`；仍可从聊天调用原工具 | demo 身份固定；专业/年级/方案来源可见；个人/通用/不可用语义与旧回归一致 |
| 推荐、教师评价、冲突与退补选压力 | 通过聊天调用现有工具并渲染结构化回答 | 不含 `schedule_constraints`/`force_calls`；独立 `check_course_conflict` 保留 |
| 官方入口与青春科大公开活动 | “校园服务”统一搜索 + `/campus/*` | 官方链接与活动回退旧回归保留；无数据时不编造 |
| 历史、重试、停止、反馈 | 聊天历史栏、回答动作与 `/conversations`、`/feedback` | anonymous 只存浏览器；demo/CAS 服务端 90 天；跨 principal 拒绝 |
| 公开证据反哺 | “知识审核”内容列表/发布治理/回答反馈 | 原文、清洗稿、diff、分块、逐块批准、复抓、来源建议与 generation 回滚已覆盖 |
| 浅深主题与移动端 | 定制“小蜗”字标、桌面窄导航、移动底栏 | 1440×1000 与 390×844，无横向溢出或控制台错误 |

故障验收映射：

| 故障 | 预期 | 证据 |
|---|---|---|
| SearXNG/Crawl4AI 未启用或健康契约不完整 | 本地问答可用；联网给出证据不足；启用联网时 readiness 失败 | `test_evidence_clients.py`、`test_sidecar_adapter.py`、`test_resilience.py` |
| 回答运行器/上游异常 | 终态为稳定 `run.failed`，不泄露异常文本或堆栈 | `test_resilience.py`；现有 LLM 降级路由旧回归 |
| 审核入队或 worker 停止 | 当前回答先完成；任务持久排队并可租约恢复 | `test_resilience.py`、`test_review_worker.py` |
| Chroma 写入失败或上一 generation 损坏 | active 指针不变，可重试；损坏版本拒绝回滚 | `test_publish_generation.py`、`test_review_governance.py`、`verify_security_ui.py` |
| 超载 | 最多 30 个回答并发，有界排队，超限返回 `503 RUN_BUSY` | `scripts/verify_web_load.py`：100 SSE、峰值 30、100 条完整结束 |

本地代码验收不等于服务器外部条件已经完成。真实 sidecar digest/运行探针、搜索引擎健康组合、可信 HTTPS 域名、CAS 白名单和服务器数据包恢复仍必须在目标服务器实测；这些条件未满足时继续使用明确的 demo/anonymous 模式，不得把演示数据伪装为真实数据。

## 15. 配置与仓库纪律

需要新增的配置模板至少包括：

```dotenv
XIAOWO_ENV=development
XIAOWO_AUTH_MODE=anonymous
XIAOWO_ADMIN_IDS=
XIAOWO_DATA_KEY=replace-with-random-key
XIAOWO_PUBLIC_ORIGIN=http://localhost:8000
XIAOWO_SEARXNG_URL=http://127.0.0.1:8080
XIAOWO_CRAWL4AI_URL=http://127.0.0.1:11235
XIAOWO_WEB_SEARCH_ENABLED=false
XIAOWO_INGESTION_WORKER_ENABLED=false
XIAOWO_SESSION_SECRET=replace-with-at-least-32-random-bytes
# XIAOWO_TRUSTED_PROXY_CIDRS=
```

上例是 `frontend/dist` 由 FastAPI 同源托管的来源。使用 Vite 开发代理时，`XIAOWO_PUBLIC_ORIGIN` 必须改为浏览器实际来源 `http://localhost:5173`，后端仍监听 `:8000`。

真实值只进入 `.env`。GitHub 只保存代码、Schema、配置模板、官方白名单、测试和文档。所有运行数据继续通过加密云盘数据包迁移。

## 16. 外部前置条件

以下事项不阻塞代码、mock 测试和比赛演示，但阻塞真实生产能力：

- 可用 HTTPS 域名或可信反向代理。
- CAS service 白名单审批。
- 服务器部署 SearXNG 与 Crawl4AI sidecar。
- 健康的 SearXNG 引擎组合和实测限速。
- 有效的科大 LLM、Embedding 与 young 配置。

这些条件未满足时必须使用显式 demo/anonymous 模式，不能以演示数据伪装真实数据。

## 17. 决策追踪

- Q1-Q12：推荐语义、冲突边界、身份与培养方案隔离、P5-3 永久回滚。
- Q1-Q18（联网讨论）：SearXNG/Crawl4AI、门控、可信来源、安全、数据反哺与页面重构方向。
- Q19-Q27：完整 Web 重构、科大配色、异步 worker、数据包、不可变快照、TTL、20 秒预算、HTML/PDF 范围。
- Q28-Q50：React/FastAPI、四工作区、SSE、来源 UI、历史、审核、会话、部署和故障边界。
- Q51-Q65：持久知识范围、可信度、白名单治理、脱敏、版本、个人数据与 HTTPS 限制。
- Q66-Q80：唯一演示身份 `PB25111691`、认证模式、交互、无上传、设计系统、数据拆分和切换门槛。

本规格没有待用户决定的开放产品问题。后续实现如需改变上述边界，必须先更新本规格并取得用户确认。

## 18. 可执行定义与状态契约

本节把前文产品语言收敛为实现规则。若前文存在概括性表述，以本节的可执行约束为准。

### 18.1 启动配置矩阵

启动时必须拒绝以下组合，而不是静默降级：

- `production + demo`。
- `cas` 且 `XIAOWO_PUBLIC_ORIGIN` 不是受信任的 `https://` 来源。
- `cas` 且缺少 CAS service URL、会话密钥或数据加密密钥。
- demo 与 CAS Provider 同时启用。
- 生产环境把 SearXNG/Crawl4AI 配置为回环、已批准容器私网以外的未授权地址。

`competition + demo` 允许 HTTP，但所有数据必须来自 demo 命名空间并持续展示演示标识。服务器本机启用的 demo 管理员也只能操作 demo 命名空间；不得通过来源地址、代理头或环境变量获得 production 发布能力。

`GET /api/v1/config/public` 只返回 UI 功能开关、认证模式、环境名、版本和公开时间预算，不得返回内部 URL、白名单明细、管理员 ID、模型名、文件路径或任何密钥状态细节。

认证与数据权限固定如下：

| principal | 公共问答 | 服务端聊天历史 | 个人学业 | 审核命名空间 | production 发布 |
|---|---|---|---|---|---|
| anonymous | 是 | 否；仅 1 小时 run 事件 | 否 | 无 | 否 |
| demo `PB25111691` | 是 | demo 数据库 90 天 | 仅合成演示数据 | `demo` | 否 |
| CAS 普通用户 | 是 | 按学号 90 天 | 当前认证学号 | 无 | 否 |
| CAS reviewer | 是 | 按学号 90 天 | 当前认证学号 | `production` | 是 |

`/conversations` 对 anonymous 固定返回 `AUTH_REQUIRED`。anonymous 结束回答时，完整答案由浏览器写入 IndexedDB；服务端只为 SSE 重连保留临时事件，过期即删除且不能列举。

### 18.2 证据按声明判定

回答先拆成原子声明，每个事实性声明独立绑定证据。来源成为 `usable` 必须同时满足：

1. 最终 URL 通过安全校验且响应为成功的 2xx。
2. 正文抽取完整、非登录墙/错误页/空壳，且实际包含支撑片段。
3. 来源没有超过对应有效期；“最新”问题必须在本轮重新验证。
4. 来源等级来自当前 Git 白名单或已审核规则，不由页面自述决定。
5. 支撑片段与声明没有实质冲突。

本地 `found=true` 要求至少一个 active 分块达到 `XIAOWO_LOCAL_RELEVANCE_MIN`，默认 `0.60`；仅有相似但过期、低可信或未支撑声明的片段仍视为未达到证据门槛。

`official_primary` 必须精确匹配白名单中的 scheme、host 和可选 path 前缀。`reliable_independent` 必须匹配审核来源规则。两个来源只有在规范化 URL、注册域、正文哈希、近重复指纹和可识别上游引用均不相同时才计为独立；任一维度显示转载关系即合并为一个证据族。

回答可以包含无须联网证明的寒暄或明确标注的建议，但不得把建议措辞包装成事实结论。

内部回答契约至少包含：

```json
{
  "claims": [{
    "claim_id": "c1",
    "text": "原子事实声明",
    "kind": "factual",
    "status": "confirmed",
    "evidence": [{
      "source_id": "s1",
      "evidence_type": "web",
      "relation": "supports",
      "excerpt_hash": "sha256",
      "citation": 1
    }]
  }]
}
```

`kind` 只能为 `factual`、`recommendation`、`chitchat`；事实 `status` 只能为 `confirmed`、`conflict`、`insufficient`。`evidence_type` 只能为 `local`、`web`、`tool`，`relation` 只能为 `supports`、`contradicts`、`context`。一个复合句包含多个事实时必须拆成多个 claim；前端引用显示由 claim 到 citation 的关系生成。

`answer.segment` 的 data 必须包含 `segment_id`、`markdown` 和 `claim_ids`；`answer.completed` 必须包含完整 claims、sources、limitations 和终态原因。引用校验失败时不得发送对应事实正文：该 claim 降为 `insufficient`，并改写为证据不足说明。

### 18.3 URL 与连接安全

用户提供 URL、搜索结果 URL、canonical URL 和每次重定向目标使用同一校验器：

- 只接受规范的 `http`/`https` URL；拒绝用户信息段、非标准 IP 文本、整数/八进制/十六进制 IP 和畸形主机名。
- 移除 fragment 和常见追踪参数；参数名或值疑似 `token`、`ticket`、`session`、`cookie`、`authorization`、`student_id`、`sid`、学号或凭证时拒绝整条 URL，而不是只删除后继续请求。
- 规范化 IPv4、IPv6 和 IPv4-mapped IPv6 后，用系统地址分类阻断所有非全局可路由地址及云元数据地址。
- 对 A/AAAA 全部结果做检查；只要混入一个非公开地址就拒绝主机。
- HTTP 客户端禁用环境代理继承和自动重定向。每跳手动解析、校验并限制最多 5 次。
- 实际连接必须使用本次已验证并固定的 IP，TLS SNI/Host 仍使用原主机；连接后核对 peer IP 属于已验证集合，防止 DNS rebinding。

Crawl4AI sidecar 必须使用其安全 egress broker，且永久禁止 `CRAWL4AI_ALLOW_INTERNAL_URLS`。FastAPI 只调用私网 adapter 的 `/health` 与 `/crawl` 契约；健康结果必须确认 `egress_protection=true`、`robots=true`、`allow_internal_urls=false`。无法确认连接 IP 固定和 peer 校验时，production 的联网模块保持关闭并使 readiness 失败，但本地问答仍可通过 liveness 提供服务。

### 18.4 查询隐私清洗

搜索查询只从当前用户消息构造，不读取原始历史。个人意图优先级高于用户选择的“联网”模式：已认证用户的问题走个人工具/本地数据且明确说明未联网；anonymous 收到个人问题时返回 `AUTH_REQUIRED`。`WEB_QUERY_UNSAFE` 只用于本应是公共问题但无法安全脱敏的情况。处理顺序固定为：

1. 若意图涉及“我的”成绩、课表、方案、画像或其他个人数据，禁止联网。
2. 删除当前认证档案中姓名、学号等精确值，并匹配学号、Ticket、Cookie、Token、成绩单和时间表结构模式。
3. 仅保留公开主题实体、时间要求和必要机构名；不得补入用户画像。
4. 再执行一次敏感模式扫描。仍有命中或清洗后语义不完整则返回 `WEB_QUERY_UNSAFE`。

查询改写模型只接收该清洗结果。系统日志只记录清洗结果的哈希、拒绝原因码和长度区间，不记录清洗前后文本。“必要公开上下文”仅指当前消息中已出现且通过清洗的公开实体，不包括历史消息。

### 18.5 Run 所有权和 SSE Schema

每个 run 绑定创建它的服务端 principal：匿名临时会话、demo 会话或 CAS 会话。匿名模式可使用 24 小时失效的不透明 HttpOnly 运行 Cookie，但不得据此上传或合并聊天历史。

读取事件、续传和取消都必须匹配相同 principal；管理员默认也不能读取其他用户 run。`run_id` 使用不可预测的随机 ID，但随机性不能替代服务端授权检查。

所有 SSE 事件具有统一包络：

```json
{
  "id": 17,
  "run_id": "opaque-id",
  "type": "stage.changed",
  "at": "2026-08-27T12:00:00Z",
  "data": {}
}
```

公开 `stage` 仅允许 `queued`、`local_retrieval`、`web_search`、`web_fetch`、`evidence_check`、`answering`、`completed`、`cancelled`。稳定错误码至少包含：`AUTH_REQUIRED`、`FORBIDDEN`、`AUTH_MODE_DISABLED`、`RUN_NOT_FOUND`、`RUN_BUSY`、`WEB_QUERY_UNSAFE`、`SEARCH_PARTIAL`、`CRAWL_BLOCKED`、`EVIDENCE_INSUFFICIENT`、`SOURCE_CONFLICT`、`UPSTREAM_TIMEOUT`、`INTERNAL_ERROR`。

来源事件只含 `source_id`、标题、展示 URL、机构/域名、发布时间、抓取时间、等级、有效状态和引用序号。不得包含本地文件路径、原始响应头、内部 IP、搜索查询或安全评分细节。

SSE `id` 是单个 run 内从 1 开始单调递增的 sequence；`Last-Event-ID` 也只在该 run 内解释。游标早于当前保留窗口时返回 HTTP 410 `EVENT_CURSOR_EXPIRED`。登出、会话轮换或 demo 登录会取消旧会话仍在运行的 run，旧 principal 此后不能读取或取消它们；已保存的终态事件按原 TTL 清理。

### 18.6 时间预算状态机

搜索和抓取共享从 run 创建开始计算的 deadline。SearXNG 在第 4 秒取消；Top 3 抓取并行且最晚在第 12 秒取消。达到声明级证据门槛后立即取消不再需要的任务并进入回答。

模型回答只发送已经闭合并通过引用检查的句子/结构块。第 18 秒取消模型后保留已经提交的完整段落，并用结构化证据模板补齐限制、冲突/不足结论和来源列表；不得重新解释或覆盖已发送内容。第 20 秒强制结束 run，并发送唯一终态事件。

客户端取消会传播到搜索、抓取和模型任务；已完成回答不能再次取消。每个 run 只能从一个终态 `completed`、`cancelled` 或 `failed` 结束。

### 18.7 入队验收规则

来源只有同时满足以下条件才可能创建审核草稿：

- `source_id` 以 `supports` 关系支撑至少一个 `confirmed` claim，且 run 正常完成；只作为冲突、上下文或证据不足列表展示的来源不得入队。
- 已保存公开快照和支撑片段，来源不是 `unverified`。
- 安全扫描无个人信息、凭证、恶意文件或 Prompt Injection 控制内容。
- URL/内容哈希没有 active 同版本；近重复只创建关联，不自动合并冲突。
- 规则分类为科大/校园内容，或模型提出“高复用通识”且审核者后续明确确认。

“高复用通识”不能由模型直接获得发布资格。模型只生成候选标签；审核员必须确认 scope、分类、TTL 和每个批准分块。

入队幂等键为 `namespace + snapshot_hash + evidence_span_hash`。已有 draft/in_review/approved/active 项时返回原任务；同一快照已 rejected 时保持 rejected，只有正文内容哈希变化或审核员显式 reopen 才创建新审核版本。

### 18.8 审核身份、白名单和审计

首期角色为 `reviewer`。CAS 模式下，身份必须来自已验证 CAS 学号且命中 `XIAOWO_ADMIN_IDS`。审计记录包含 actor principal、认证模式、动作、对象版本、前后哈希、时间和请求 ID，不保存凭证。

demo 管理员只能操作 `namespace=demo`；demo 发布写入隔离索引，不能被 anonymous/cas 检索。没有本机或环境变量例外可以把 demo 身份提升为 production 发布者；production 审核与发布必须等待 HTTPS 下的 CAS 管理员。

白名单建议进入 `source_trust_proposals`，审核页只能导出建议 diff。正式变更必须修改 `config/source_trust.yaml`，经测试、Git 审查和部署生效；回滚使用上一 Git 版本。规则必须包含精确 host、可选 path 前缀、来源等级、机构名、生效时间和依据说明。

### 18.9 发布状态机

知识版本状态固定为：

```text
draft -> in_review -> approved -> pending_publish -> active
                     \-> rejected
pending_publish -> publish_failed -> pending_publish
active -> expired | revoked
```

发布使用不可变 `generation_id`。Chroma 先写入带 generation/version 元数据的非 active 文档；BM25 写入新的 generation 文件并以临时文件原子重命名。两边校验完成后，在 review.db 单事务中切换 active generation。检索以 review.db 的 active 清单为权威，不能读到仅写入一边的版本。

worker 恢复 `pending_publish`/`publish_failed`，使用幂等键避免重复。回滚只切回上一完整 generation，不在原索引上做部分逆操作。

BM25 generation 文件和 manifest 在原子重命名前必须写入并 fsync 文件及父目录；Chroma 写入后校验文档数和内容哈希。若 SQLite 切换前崩溃，该 generation 视为孤儿且不可检索，恢复任务可复用校验通过的产物；切换后崩溃则以 SQLite active 指针和 manifest 校验恢复。孤儿 generation 保留 7 天后清理。

单个分块过期、撤回或重新批准都通过构建下一完整 generation 生效，不在 active generation 上原地删除，从而保持 Chroma/BM25 一致。

### 18.10 缓存、反馈和保留

证据优先级为：本轮重新验证的一手来源、其他本轮可靠来源、未过期 active 本地知识、明确标记的历史缓存。历史缓存不能单独回答“最新”问题；条件请求失败时返回证据不足，不延用旧有效状态。

“当前”检测至少包含“最新、今天、现在、当前、截至、刚刚、本周、本月、今年、目前、现行、还有效吗”，并由时效意图分类补充；分类器只能增加联网要求，不能取消显式关键词触发。

回答反馈存于 xiaowo.db 的独立 `answer_feedback` 表，字段只含 answer/run 引用、分类、可选说明、状态和时间。可选说明最多 1000 字符，写入前执行与联网查询相同的敏感扫描，命中时返回 `FEEDBACK_SENSITIVE`；允许保存时使用数据密钥加密。说明不进入知识库，30 天后删除；管理员转为知识复核任务时只复制 answer/source 引用、分类和重新脱敏后的摘要，不复制原说明。

死信任务保留 90 天供诊断。审核审计和内容哈希长期保留。不可变原始快照在存在审核/批准引用时保留；法律、隐私或管理员强制清除时删除内容文件，但保留不含正文的哈希墓碑和审计事件。

### 18.11 仓库资产定义

`knowledge/data/**/*.md` 是人工维护的权威源文档，继续进入 Git；`knowledge/chroma_db/` 是生成索引，不进入 Git。新前端 `package-lock.json` 和后端依赖声明属于代码构建元数据，应进入 Git。

当前未跟踪的 `requirements.lock.txt` 保持原状，本任务不得顺手纳入或删除；Python 可重复锁定方案在依赖迁移阶段以单独变更处理。所有数据库、网页快照、审核内容和生成索引继续不入 Git。

### 18.12 HTTPS、代理与完整配置校验

CAS 的可信来源只等于 `XIAOWO_PUBLIC_ORIGIN` 配置的精确 `https` scheme、规范化 host 和显式/默认 port。回调 service URL 必须属于该来源。应用不根据任意请求头推断 HTTPS。

默认不信任 `Forwarded` 或 `X-Forwarded-*`。只有直接对端 IP 命中 `XIAOWO_TRUSTED_PROXY_CIDRS` 时才解析这些头，且代理必须覆盖而不是追加客户端传入值。CAS 模式还必须配置至少 32 随机字节的 `XIAOWO_SESSION_SECRET`，用于登录 state、CSRF 派生和会话相关 HMAC；数据加密继续使用独立 `XIAOWO_DATA_KEY`。

未知 `XIAOWO_ENV`/`XIAOWO_AUTH_MODE`、`anonymous + XIAOWO_ADMIN_IDS`、production demo、HTTP CAS 均使进程启动失败。`XIAOWO_WEB_SEARCH_ENABLED=true` 时，readiness 必须验证 SearXNG/Crawl4AI 位于配置的私有地址且健康/安全能力匹配；失败时不能偷偷继续联网。

安全验收必须使用固定夹具覆盖：IPv4/IPv6/映射地址、混合 DNS、DNS rebinding、每跳重定向、敏感 URL 参数、SSE 跨 principal 与过期游标、重复入队、发布各崩溃点、分块过期 generation，以及反馈敏感文本。
