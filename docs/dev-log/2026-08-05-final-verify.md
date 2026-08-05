# 2026-08-05 final-verify（收尾：全量验证）

## 目标

全量验证 6 项 finding 修复（fix1 基线 → fix2 登录实测 → fix3 silent-fallback → fix4 幂等同步 → fix5 配置统一 → fix6 文档对齐 → fix7 dev-log），确认每项修复有独立提交、无敏感文件入库、端到端链路可用。

## 验证结果

| 检查项 | 结果 |
|--------|------|
| git 提交独立性 | 6 个提交各自对应一个 finding，`git log --name-only` 确认 dev-log 随对应提交入库 ✓ |
| 工作区清洁 | `git status` 干净；`.env`、`database/*.db`、`knowledge/chroma_db/`、`.qoder/` 均未被追踪 ✓ |
| init_check | 无回归 ✓ |
| py_compile | `services/cas_client.py` 等通过 ✓ |
| 浏览器冒烟 | streamlit (localhost:8501) 启动正常，UI 完整渲染（"知识库: 50 篇文档 \| 未登录"）✓ |
| 未登录端到端查询（浏览器） | 输入"帮我查一下我的成绩"→ 返回成绩表格 + "⚠️ 教务接口暂时不可用，以上为本地缓存成绩，仅供参考"来源标注（修复 3 端到端生效）✓ |
| Python 侧完整链路复现 | course agent 链路 10.9s 返回，来源标注正确 ✓ |

## 新发现

1. **LLM 平台冷启动延迟**：首次请求实测 30.9s（超过 `LLM_CONFIG["timeout"]=30s`），浏览器首次查询报 "Request timed out."；预热后同请求 0.3s。属平台侧冷启动特性，非代码缺陷。建议后续将 timeout 提升至 60s 或在部署前预热（本次未改代码，超出 finding 范围）。
2. **演示学生未登录行为确认**：`query_grade` 对演示学生（PB20240001）未登录返回 `source=fallback` 本地缓存 + 来源标注；真实学号未登录才返回 `source=locked`。浏览器实测与 `verify_not_logged_in.py` 结论一致，属设计行为。

## 遗留问题

- 应用内完整 CAS 链路（跳转 → ticket → 回调）依赖 CAS service 白名单，部署前不可验证（见 login-e2e 记录）
- LLM 平台冷启动可能造成用户首次查询超时（见上）
- streamlit 日志被 transformers 模块 watcher 噪音淹没（torchvision 缺失的 ModuleNotFoundError 反复打印），排查应用日志时需过滤
