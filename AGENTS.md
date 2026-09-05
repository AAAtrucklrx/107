# AGENTS.md — 小蜗（Xiaowo）仓库给 AI 代理的工作说明

> 面向在**本仓库**内工作的 AI 代理（如 DeepSeek Harness 中的 agent）。首次工作前必读本文档 + `docs/VM接手文档.md` + `docs/部署规格与记录_2026-09-01.md`（完整上下文），并按“首次工作建议步骤”开始。人用文档见 `docs/会话交接摘要.md`。

## 项目一句话

小蜗 = 科大校园智能助手（107 杯比赛项目）：**React/Vite 四工作区 Web（FastAPI `/api/v1` + SSE）为主应用**，LangGraph 统一 QA（意图分类 → think ≤4 轮 → act 工具 **（单轮 ≤3 并行）** → compose），SQLite 双库 + ChromaDB 混合检索 + **结构化数据卡**（成绩/课表/考试/选课/活动/培养方案等 13 类工具结果直接表格渲染，不经 LLM 重述）+ **语义答案缓存**（发布 hash 失效）+ **世界知识通道**（非校内常识 LLM 直接答+「非联网核实」免责）+ **今日弹窗**（课程+活动，登录即弹）+ 31 注册工具（think 提示目录 28 + `eco:` 生态）。校内服务（教务/CAS/青春科大 young/**DeepSeek 官网 LLM**）。Streamlit（`app_test.py`）仅作回退入口。

## ⚠️ 当前状态（2026-09-01 实测）——动手前必看

- HEAD：`cf6fea2`（feat(search): recency words… 2026-09-05），main 分支，远端 `github.com/AAAtrucklrx/107`。
- **工作区干净**（2026-09-03 提交后）：无未提交改动；未跟踪项均为数据/环境产物（`data/`、`database/*.db-wal/shm`、`scripts/data/`、`scripts/tmp_*`、`.models/`、`.npm-cache/`、`deploy/server/logs|run/`、`deploy/sidecars/` 等，均不入 git）。
- **数据安全体系（2026-09-03 上线）**：demo reset 默认禁用+密钥+清空前自动导出；每日备份 `deploy/server/backup_daily.sh`（7 项，日 7 份+周 5 份）；业务哨兵 `sentinel.py`（readiness 的 approved_index/search_quality）；数据事故恢复 SOP 见 `docs/部署规格与记录_2026-09-01.md` §14/§16。**纪律：外部小蜗包（云盘/旧机）不得解压覆盖工作区——尤其 data/ 与 .env（2026-09-03 曾致 review.db 损坏，已恢复）。**
- **已确认的推荐边界**（用户定案，不得重新引入）：推荐只处理课程选择；课程范围是硬条件；兴趣/工作量/教师/目标学期默认软排序，只有“只要/必须”升级为硬过滤；复合“推荐且不冲突”只推荐并说明未查课表；独立 `check_course_conflict` 保留；`force_calls`/`pending_force_calls` 已永久移除。
- **已确认的身份边界**：登录后的专业/年级只取当前用户 CAS/成绩档案，取不到不猜且不继承匿名选择；个人方案失败时可按已验证身份显示通用方案，但必须标注“专业通用参考，不是个人培养方案”。
- 规则：动手前 `git status` + `git diff`，只改任务声明的文件；改完跑全量验证并报告；**不自提交**（经批准后按铁律 3 提交）。

## 部署现状（本服务器 = 生产/比赛环境，已完整部署）

| 服务 | 端口 | 说明 |
|---|---|---|
| Web（SPA+API+SSE） | 8000（公网 8850 转发） | `competition + demo`，公网访问 `http://114.214.241.119:8850`（origin 单值校验，改地址须同步 `.env` 的 `XIAOWO_PUBLIC_ORIGIN`）；**联网已启用**（readiness 四项全绿：database/review_database/web_evidence/evidence_extractor） |
| 审核/发布 worker | — | `python -m xiaowo_web.worker`，常驻 |
| Streamlit 回退 | 8502 | 仅紧急回退 |

- 公众号联网通道：科大相关问题优先检索微信公众号（`XIAOWO_WECHAT_ENABLED=true`，官方号白名单 `中国科学技术大学|中科大|中国科大|蜗壳`，图片 OCR 走平台 unlimited-ocr；熔断+限频+SSRF 域白名单保护；详见 `docs/公众号联网通道_spec_2026-09.md` 与 `docs/部署规格与记录_2026-09-01.md` §11）。
- 联网 sidecar（宿主机 Docker 29.7.2，已 attestation）：SearXNG `127.0.0.1:8080` + Crawl4AI 0.9.2 + adapter `127.0.0.1:11235`，三容器 healthy；管理经 `/var/run/docker.sock`（沙箱内可 exec/restart，脚本 `deploy/server/docker_exec.py`）或宿主机 `docker compose -f deploy/sidecars/compose.yml`。**搜索源已切换 `XIAOWO_SEARCH_PROVIDER=bocha`**（2026-09-03 晚，博查 Web Search API 国内直连；SearXNG 引擎组 5 引擎全封降为备用）；sentinel 已跟随 provider；sidecar 细节与调优见 `docs/部署规格与记录_2026-09-01.md` §7。
- 管理：`deploy/server/{start_all,stop_all,status}.sh`（nohup + pidfile，**无 systemd**）；日志 `deploy/server/logs/`。
- 健康：`GET /api/v1/health/live`、`/api/v1/health/ready`、`/api/v1/config/public`。
- `.env` 是服务器唯一配置：**LLM=DeepSeek 官网**（`LLM_BASE_URL=https://api.deepseek.com/v1`，`deepseek-v4-flash`，`thinking: disabled` 经 extra_body 关闭，OCR=`deepseek-v4-flash-vision-exp`）、YOUNG_TOKEN、`XIAOWO_ENV=competition`、`XIAOWO_AUTH_MODE=demo`、`XIAOWO_PUBLIC_ORIGIN`、搜索 `XIAOWO_SEARCH_PROVIDER=bocha`+BOCHA key、预算（生成 120s / run 150s / 证据 40s）。密钥不进 git。
- **2026-09-05 新增可观测行为**：事件流含 `data.table`（结构化卡：工具完成即推，先于正文）、`answer.delta`（compose 增量流式）、`stage.changed(action=…)`（动作播报：意图/并行查询/✅工具/信息已齐）、占位段（run 开始即出字）；前端行为：登录即弹「今日卡」（Radix Dialog）、打字机渐显、Stripe 收起。**演示要点**：时事类问题请切「网页模式」（auto 对"最近/最新"类会联网核验但置信门槛严格，可能如实降级）。

## 环境事实（Linux 服务器 · 2026-09-01 实测）

- Ubuntu 24.04 LTS，8C/16G；DSH 运行于 bwrap 沙箱（PID1=bwrap）：**/etc、/root 只读**（无 systemd 总线、无系统 pip/ensurepip），工作区 `/root/Desktop/小蜗` 为宿主机磁盘挂载（持久化）。
- Python 3.12.3；仓库 venv：`.venv`（get-pip 引导，pip 26.2.1）→ 一律用 `.venv/bin/python` / `.venv/bin/pip`。
- Node v22.23.2 / npm 10.9.8；npm 缓存必须指工作区：`--cache /root/Desktop/小蜗/.npm-cache`（默认 `/root/.npm` 只读，报 EROFS）。
- 依赖：`requirements.lock.txt`（163 锁定，**缺 fastapi/pytest 两个锁文件遗漏项**，已补装）；前端 lock 已含全部依赖。
- 网络：**DeepSeek 官网 ✓**（`api.deepseek.com/v1`，`deepseek-v4-flash`/`-v4-pro`/`-flash-vision-exp`；2026-09-04 自 `api.llm.ustc.edu.cn` 切换）、PyPI/npm/hf-mirror ✓；**github.com 仅经 Watt Toolkit 加速通道**（见下；加速器进程若死：`nohup /root/Downloads/dotnet/dotnet /root/Downloads/modules/Accelerator/Steam++.Accelerator >/tmp/accel.log 2>&1 &` 即恢复 443）。
- **Git 通道（Watt Toolkit / Steam++）**：本机 DNS 将 github.com/api.github.com 解析到 127.0.0.1，`Steam++.Accelerator` 监听 0.0.0.0:443 代发。**根证书 2026-08-31 已轮换**，当前 CA = `scripts/data/steamtools_ca_latest.pem`（本地文件，不入库；旧 `steamtools_ca.pem` 已失效）。Linux 版 git 为 **GnuTLS 后端，无 openssl 后端**（接手日志里 `-c http.sslBackend=openssl` 的命令在此机器直接报错）：
  ```bash
  # pull（匿名可读）
  git -c http.sslCAInfo=scripts/data/steamtools_ca_latest.pem pull origin main
  # push（需 PAT，用完提醒吊销）
  git -c http.sslCAInfo=scripts/data/steamtools_ca_latest.pem \
      -c http.extraheader="AUTHORIZATION: basic $(printf 'x-access-token:<PAT>' | base64 -w0)" push origin main
  ```

## 必备命令

```bash
PY=/root/Desktop/小蜗/.venv/bin/python
# 全量验证（改后必跑；LLM 依赖项需校内 LLM 可达）
$PY scripts/verify_tools.py    # 44/44（2026-09-01 基线）
$PY scripts/test_fixes.py      # 49/49
$PY scripts/verify_nodes.py    # 66/66
$PY scripts/check_course_db.py # 9/9
$PY scripts/verify_ecosystem.py; $PY scripts/verify_links.py
$PY scripts/verify_profile.py; $PY scripts/verify_time_parser.py
$PY scripts/verify_security_ui.py   # 20/20
$PY scripts/verify_activities.py    # 需 YOUNG_TOKEN，失效自动 SKIP
$PY -m pytest tests/web -q          # 225 passed（2026-09-05 实测）
# 需 LLM（向外部发送学号/画像，需授权）：scripts/qa_consistency.py 12/12 · scripts/qa_new_docs.py 10/10
$PY init_check.py   # 数据库/评课库/知识库校验（含 db_manager 轻量迁移）
# 前端（改 frontend/ 后）：cd frontend && npm ci --cache ../.npm-cache && npm run build
# 部署操作：./deploy/server/{start_all,stop_all,status}.sh
```

## 铁律

1. **不造假**：接口不可用如实返回“暂无数据”，禁止编造/推测（空教室/成绩/冲突结论一律以工具结果为准）。
2. **来源标识**：降级/缓存数据必须带来源（实时数据/本地缓存/第三方工具），回答附官方 URL 只在工具返回时透出。
3. **git**：不自提交；改动清单 + 验证结果报给用户，经批准后 `git -c user.name="xiaowo-dev" -c user.email="xiaowo@local" commit`（message 用单引号）；推送走 Watt 通道（见上，需 PAT，不用 Windows 凭据方案）。
4. **密钥**：`.env`（LLM_API_KEY/YOUNG_TOKEN）、DSH API key、GitHub PAT 不进 git、不贴聊天/群。
5. **测试断言**：`any` 勿用问句回显词；`banned` 含 `.*` 时按正则匹配（qa_consistency 约定）。
6. **文档同步**：改行为必同步 `docs/tool-specs.md`/`docs/总纲与工具接口方案.md`/`README.md`/`docs/部署规格与记录_2026-09-01.md` 中相关描述（P6-3 未完成，漂移常见）。

## 首次工作建议步骤

1. 读 `docs/VM接手文档.md` + `docs/部署规格与记录_2026-09-01.md`（完整事实：架构/数据/命令/待办/部署状态）。
2. `git status` + `git diff` 确认工作区；未跟踪文件均为数据/环境产物，勿 add 入库。
3. 跑全量验证（§必备命令），记录实际基线，不要沿用旧页面的旧计数。
4. 推荐与培养方案产品语义已由用户确认；新改动不得重新引入排课偏好过滤、`force_calls` 或跨用户身份兜底。
5. 部署类改动：改完 `./deploy/server/status.sh` + 健康端点 + 一条问答/接口冒烟再报告。
