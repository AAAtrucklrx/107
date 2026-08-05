# 2026-08-05 instruction-data-drift

## 目标

finding: `instruction-data-drift`（Medium，仅文档与注释对齐，代码行为不改）。

修正文档声明与代码/实测不符处：知识库数量、LLM 模型名、明文密钥、种子数据来源、embedding 能力描述。

## 改动文件

- `项目交接报告.md`：知识库 12 篇/4 分类 → 50 篇/5 分类（办事10/就业5/教务15/生活13/科研与升学7，共 3 处）；模型 `deepseek-v4-flash` → `deepseek-chat`（2 处）；删除第 243 行明文密钥，改为「经环境变量 `LLM_API_KEY` 注入，见 `.env`（不入库）」；种子数据来源如实化（课程/课表/考试基于真实 catalog 抓取，成绩/评课为登录前演示数据）；Embedding 行更新为 qwen3-embedding 实测可用；踩坑 8 更新为 2026-08-05 实测；报告版本 v1.0 → v1.1
- `docs/tool-specs.md`：向量检索 12 篇 → 50 篇；「已知问题」更新（API embedding 实测可用 + `THRESHOLD_MAP` 阈值说明）；空教室「当前实现问题」改述为「降级路径说明」（主路径已走 catalog API，模拟逻辑仅存在于带来源标识的 fallback）；评课种子 10 门 → 8 门
- `成果说明书.md`：FAQ 12 篇/4 分类 → 50 篇/5 分类（含目录树）；seed 数据数量如实化（8 门课/8 条课表/6 条成绩/8 门评课/3 位教师/3 个日程）；Python 文件 27 → 50；Tool 16 → 20；代码行数约 2000+ → 约 6800
- `产品说明.md`：FAQ 分类注释「教务/生活/科研/社团/其他」→「办事/就业/教务/生活/科研与升学」
- `database/seed_data.py`：头部注释如实描述数据构成（课程/课表基于真实 catalog 抓取；成绩/评课为演示数据，登录后由 jw API 实时拉取）
- `init_check.py`：分类打印列表补上「就业」（此前只打印 4 分类，漏 5 篇就业文档）

## 验证

- `grep sk-[A-Za-z0-9]{8,}`：0 处明文密钥残留 ✓
- `grep "12 篇|教务 5 / 生活 4|deepseek-v4-flash"`：仅剩 1 处为新写入的踩坑记录事实（deepseek-chat 实际路由至 `deepseek-v4-flash-ascend`）✓
- `py -m py_compile database/seed_data.py`：通过 ✓
- `py init_check.py`：50 篇 5 分类全打印（办事10/就业5/教务15/生活13/科研与升学7），API embedding 检索命中（score 0.5731）✓
- 知识库实测计数与文档声明一致（`knowledge/data/` 下 50 篇 .md）

## 附加发现（已写入踩坑记录，未改代码行为）

- 实测 `GET /v1/models`：key 白名单含 `deepseek-chat`、`deepseek-v4-flash`、`qwen3.6-chat` 等；`deepseek-chat` 请求会路由至 `deepseek-v4-flash-ascend`（推理模型），`max_tokens` 过小（如 10）时输出全部进入 `reasoning_content`，`content` 为 null；`max_tokens` 充足时经 LangChain `create_llm().invoke()` 实测 `content='好的'` 正常。`qwen3.6-chat` 亦实测可用。

## 遗留问题

- `deepseek-chat` 为推理类模型（响应先出 `reasoning_content`），若应用侧出现回答为空，优先检查 `max_tokens` 是否过小
- `docs/dev-log/` 为本次任务新目录，后续每个任务按 README 约定记录
- 修复 2（登录实测）仍等待用户 CAS ticket
