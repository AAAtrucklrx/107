# VM 部署运维说明(小蜗 · 东风云 114.214.241.119)

> 2026-08-26 由 DSH 代理在 VM 上按交接文档落地。配套文档:`docs/VM接手文档.md`(事实基线)、`docs/会话交接摘要.md`(恢复起点)。
> 本文件记录:① 平台端口转发配置步骤(需用户在控制台操作);② NSSM 服务注册命令(服务稳定后执行);③ 日常运维速查。

---

## 1. 平台端口转发配置(需用户在东风云控制台操作)

### 1.1 目标

| 项 | 值 |
|---|---|
| 云主机内服务 | Streamlit `app.py`,监听 **8501** |
| 平台源端口(虚拟 IP) | **8850**(平台规则:源端口 → 云主机端口) |
| 校内访问网址 | `http://114.214.241.119:8850` |
| 源端口禁用清单 | 22 / 53 / 514 / 123 / 7272 / 7274(平台限制,8850 不受影响) |

### 1.2 配置步骤(参考 RDP 已建规则 23389→3389 的做法)

1. 登录东风云控制台 → 弹性云主机 → 网络/安全组 → **端口转发(三层网络)**。
2. 新建规则:
   - 源端口(虚拟 IP 侧):`8850`
   - 目标(云主机):`8501`
   - 协议:TCP
3. 保存后,在校内网络/iWan 下访问 `http://114.214.241.119:8850` 验证。
4. **验证后需同步修改 `C:\小蜗\.env`**:
   ```
   CAS_SERVICE_URL=http://114.214.241.119:8850
   ```
   并重启服务(CAS 回调地址必须与白名单/实际访问地址一致,否则 CAS 登录回调会失败)。
5. **P3-1 CAS 白名单**:向网络中心登记 service URL = `http://114.214.241.119:8850`(外部申请,审批周期不可控,是正式登录的前置)。

### 1.3 公网访问(可选,用户决定)

- 如需公网访问,另行提交「出校」申请;审批通过后才有公网入口。
- 公网地址确定后,同样需同步 `CAS_SERVICE_URL` 与白名单。

---

## 2. NSSM 服务注册(服务稳定后执行,当前为手动后台运行)

> 约定(2026-08-26 用户确认):先手动后台运行验证稳定,稳定后再注册 NSSM,避免服务自启与调试互相干扰。

### 2.1 安装 NSSM(如未安装)

```powershell
# 下载 nssm 2.24(64 位)并解压到 C:\tools\nssm
# 或 winget install NSSM.NSSM(如可用)
```

### 2.2 注册命令(管理员 PowerShell)

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
$dir = "C:\小蜗"

nssm install Xiaowo $py "-m streamlit run app.py --server.port 8501 --server.headless true"
nssm set Xiaowo AppDirectory $dir
nssm set Xiaowo AppStdout "$dir\data\service.stdout.log"
nssm set Xiaowo AppStderr "$dir\data\service.stderr.log"
nssm set Xiaowo Start SERVICE_AUTO_START
nssm set Xiaowo AppExit Default Restart   # 崩溃自动重启
nssm set Xiaowo AppRestartDelay 5000      # 5 秒后重启
nssm start Xiaowo
```

### 2.3 服务日常操作

```powershell
nssm restart Xiaowo      # 重启(改代码后)
nssm stop Xiaowo         # 停止
sc query Xiaowo          # 查看状态
Get-Content C:\小蜗\data\service.stderr.log -Tail 50   # 看日志
```

> ⚠️ 重启前若曾手动启动过 8501,先杀净全部监听 8501 的 PID(孤儿进程共享监听会随机路由到旧实例):
> `Get-NetTCPConnection -LocalPort 8501 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`

---

## 3. 日常运维速查

### 3.1 启动 / 停止(手动模式)

```powershell
# 正式版(8501)
$py = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
Start-Process -WindowStyle Hidden $py -ArgumentList '-m','streamlit','run','C:\小蜗\app.py','--server.port','8501','--server.headless','true' -WorkingDirectory 'C:\小蜗'

# 测试版(8502,离线数据,伪登录 PB25111691)
Start-Process -WindowStyle Hidden $py -ArgumentList '-m','streamlit','run','C:\小蜗\app_test.py','--server.port','8502','--server.headless','true' -WorkingDirectory 'C:\小蜗'
```

### 3.2 验证集(改动后全跑)

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
& $py scripts/verify_tools.py        # 40/40
& $py scripts/test_fixes.py          # 50/50
& $py scripts/verify_nodes.py        # 53/53
& $py scripts/check_course_db.py     # 9/9
& $py scripts/verify_profile.py      # 11/11
& $py scripts/verify_ecosystem.py    # 10/10
& $py scripts/verify_links.py        # 12/12
& $py scripts/verify_activities.py   # 8/8(token 失效自动 SKIP)
& $py scripts/verify_time_parser.py  # 17/17
& $py scripts/verify_security_ui.py  # 20/20
# 需 LLM 授权:qa_consistency 12/12 · qa_new_docs 10/10(会向校内 LLM 发送测试学号/画像)
```

### 3.3 数据运维

```powershell
& $py scripts/crawl_young.py                  # young 快照刷新(换 YOUNG_TOKEN 后)
& $py scripts/refresh_course_db.py --dry-run  # 评课库重爬 SOP(选课季前)
& $py init_check.py                           # 初始化校验
& $py rebuild_kb.py --yes                     # 向量库全量重建(维度不匹配/降级 BM25 时)
```

---

## 4. 本 VM 部署记录(2026-08-26)

| 项 | 状态 |
|---|---|
| 代码 | `C:\小蜗`,git HEAD = `510f22c` = origin/main **完全同步**,工作区零修改(可溯源 ✅) |
| Python 3.14.7 | ✅ 已装(winget 别名损坏不可用,改直装官网安装器;路径 `%LOCALAPPDATA%\Programs\Python\Python314\python.exe`) |
| git 2.55.0 | ✅ 已装(`C:\Program Files\Git\cmd\git.exe`;GitHub 可达性实测 OK) |
| VC++ 运行库 | ✅ 已更新至 14.44(SYSTEM32 原为 2016 版 MSVCP140,导致 chromadb 原生库访问冲突崩溃,已修复) |
| 依赖 | ✅ venv `C:\小蜗\.venv` + `pip install -r requirements.lock.txt`(163 包全装) |
| 向量库 | ✅ 已重建:769 向量(API 嵌入 qwen3-embedding)、元数据零缺失、6/6 检索命中;原拷贝的 chroma_db 无法加载(hnsw 损坏),`rebuild_kb.py --yes` 解决 |
| 验证集 | ✅ **10/10 套件全过**:verify_tools 40/40 · test_fixes 50/50 · verify_nodes 53/53 · check_course_db 9/9 · verify_profile 11/11 · verify_ecosystem 10/10 · verify_links 12/12 · verify_activities 8/8(实拉) · verify_time_parser 17/17 · verify_security_ui 20/20;init_check ✅ |
| 服务 | ✅ **运行中**(手动后台,Streamlit 8501,HTTP 200);冒烟:知识问答 RAG ✅ / 选课推荐工具链 ✅ / 闲聊 ✅ |
| LLM 平台 | ✅ api.llm.ustc.edu.cn 可达,key 有效(嵌入+合成均实测) |
| YOUNG_TOKEN | ✅ 有效(verify_activities 实拉 8 条;过期会自动回退快照) |
| 端口转发 | ⏳ 待用户在平台执行(本文件 §1);转发生效后需同步 `.env` 的 `CAS_SERVICE_URL` 并重启服务 |
| NSSM | ⏳ 待服务稳定确认后注册(命令见 §2) |

> ⚠️ 与交接文档的差异:文档快照(2026-08-25)记载 HEAD=28e53f7+未提交改动;实际开发机已提交并推送为 **510f22c**(fix: isolate program plans and refine recommendations),VM 代码即该最新状态。文档(VM接手文档/会话交接摘要)中的 28e53f7 描述待后续同步(需用户批准改文档)。
