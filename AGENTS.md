# AGENTS.md — 小蜗（Xiaowo）仓库给 AI 代理的工作说明

> 面向在**本仓库**内工作的 AI 代理（如 DeepSeek Harness 中的 agent）。首次工作前必读本文档 + `docs/VM接手文档.md`（完整上下文），并按“首次工作建议步骤”开始。人用文档见 `docs/会话交接摘要.md`。

## 项目一句话

小蜗 = 科大校园智能助手（107 杯比赛项目）：Streamlit + LangGraph 统一 QA（意图分类 → think ≤4 轮 → act 工具 → compose 合成），SQLite 双库 + ChromaDB 混合检索 + 28 内置工具（+ `eco:` 生态工具）+ 校内服务（教务/CAS/青春科大 young/科大 LLM 平台）。

## ⚠️ 工作区现状（2026-08-25 实测）——动手前必看

- HEAD：`28e53f7`（fix: harden auth, scheduling, and Streamlit flows），main 分支，远端 `github.com/AAAtrucklrx/107`。
- **工作区有未提交的进行中改动**，主题：推荐语义收敛、认证档案与培养方案多用户隔离、P5-3 永久回滚、对应测试和文档。`force_calls`、`pending_force_calls`、推荐侧 `schedule_constraints` 与方案页三按钮均已删除；每课只保留一个“问问小蜗”。**细节见 `docs/VM接手文档.md` §2。**
- **已确认的推荐边界**：课程范围是硬条件；兴趣/工作量/教师/目标学期默认软排序，只有“只要/必须”升级为硬过滤；复合“推荐且不冲突”只推荐并说明未查课表；独立 `check_course_conflict` 保留。
- **已确认的身份边界**：登录后的专业/年级只取当前用户 CAS/成绩档案，取不到不猜且不继承匿名选择；个人方案失败时可按已验证身份显示通用方案，但必须标注“专业通用参考，不是个人培养方案”。
- 2026-08-25 本地验证基线已更新，当前没有旧版所述的两项失败。
- 规则：**不得擅自提交/还原这些未提交改动**；动手前 `git status` + `git diff`，只改任务声明的文件；改完跑全量验证并报告。

## 环境事实（Windows）

- Python 3.14：`C:\Users\Richelieu\AppData\Local\Programs\Python\Python314\python.exe`（`python` 是占位符）。
- PowerShell 无 `&&`（用 `;`）；内联 `python -c "..."` 多行/引号易炸 → 写临时脚本文件再跑。
- 受限沙箱测试：设 `XIAOWO_TEST_TMP=F:\小蜗\scripts\data\tmp_test`（沙箱 tempfile 被拒时）；`verify_security_ui.py` 在沙箱 tempfile 权限问题下跑不了（WinError 5），在真实环境/VM 上可跑。
- 数据库/向量库等都在仓库相对路径下（`config.py` 全部 `PROJECT_ROOT` 派生），无硬编码路径；但 `scripts/data/*.db`、`knowledge/chroma_db/`、`scripts/data/young_personal/` 等**数据产物不入 git**（.gitignore），新环境需手动迁移（部署数据包见接手文档 §5）。

## 必备命令

```powershell
# 全量验证（改后必跑；LLM 依赖项需校内 LLM 可达）
$py = 'C:\Users\Richelieu\AppData\Local\Programs\Python\Python314\python.exe'
& $py scripts/verify_tools.py    # 40/40（基线）
& $py scripts/test_fixes.py      # 50/50（基线）
& $py scripts/verify_nodes.py    # 53/53（基线）
& $py scripts/check_course_db.py # 9/9
& $py scripts/verify_ecosystem.py; & $py scripts/verify_links.py
& $py scripts/verify_profile.py; & $py scripts/verify_time_parser.py
& $py scripts/verify_security_ui.py   # 20/20（需可写临时目录）
& $py scripts/verify_activities.py    # 需 YOUNG_TOKEN，失效自动 SKIP
# 需 LLM：scripts/qa_consistency.py 12/12 · scripts/qa_new_docs.py 10/10

# 启动（演示/测试版 8502；正式 8501）
& $py -m streamlit run app_test.py --server.port 8502 --server.headless true
# 重启前：netstat -ano | Select-String ":8502.*LISTENING" 杀净全部 PID（孤儿进程共享监听）
```

## 铁律

1. **不造假**：接口不可用如实返回“暂无数据”，禁止编造/推测（空教室/成绩/冲突结论一律以工具结果为准）。
2. **来源标识**：降级/缓存数据必须带来源（实时数据/本地缓存/第三方工具），回答附官方 URL 只在工具返回时透出。
3. **git**：不自提交；改动清单 + 验证结果报给用户，经批准后 `git -c user.name="xiaowo-dev" -c user.email="xiaowo@local" commit`（pwsh 执行，message 用单引号；沙箱 push 方案见 `docs/接手日志.md` ⚡ 节“沙箱内 git push 方案”，VM 上正常 git 即可）。
4. **密钥**：`.env`（LLM_API_KEY/YOUNG_TOKEN）、DSH API key 不进 git、不贴聊天/群；只在本地与 VM .env 间复制。
5. **测试断言**：`any` 勿用问句回显词；`banned` 含 `.*` 时按正则匹配（qa_consistency 约定）。
6. **文档同步**：改行为必同步 `docs/tool-specs.md`/`docs/总纲与工具接口方案.md`/`README.md` 中相关描述（P6-3 未完成，漂移常见）。

## 首次工作建议步骤

1. 读 `docs/VM接手文档.md`（完整事实：架构/数据/命令/待办/部署进行中状态）。
2. `git status` + `git diff` 确认工作区，保留并理解未提交的推荐/身份/P5-3 回滚改动。
3. 跑全量验证，记录实际基线；不要沿用 2026-08-22 的旧计数。
4. 当前推荐与培养方案产品语义已由用户确认；新改动不得重新引入排课偏好过滤、`force_calls` 或跨用户身份兜底。
