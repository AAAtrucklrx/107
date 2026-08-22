# 校园生态工具投稿指南（注册协议 v1）

> 目标：同学自制的校园小工具（GPA 计算器、课表导出、信息聚合等）可以投稿接入小蜗，
> 由小蜗在对话中按需调用并**署名**。试点期采用白名单制：投稿经开发组审核后合入本目录。

## 三步接入

1. **写 Spec**：`tools/ecosystem/{tool}.spec.yaml`（字段见下，工具名必须 `eco:` 前缀）；
2. **写实现**：同目录 `{tool}.py`，暴露 `run(params: dict, ctx: dict) -> dict`；
3. **提交审核**：无需改任何框架代码，加载器自动扫描注册（`agents/tool_registry.py` 合并）。

## Spec 必填字段

| 字段 | 说明 |
|---|---|
| `name` | 工具名，必须 `eco:` 前缀（如 `eco:gpa_predict`） |
| `display_name` | 中文名（UI 与工具清单展示） |
| `provider` | 提供者署名（如 `张三（PB21xxxxx）`），回答中会透出 |
| `description` | 一句话功能描述（think 决策依据，写清楚"何时该用"） |
| `version` | 语义化版本 |
| `permission` | `read_only`（默认）或 `write`（写操作需额外审核） |
| `auth_required` | 是否需要登录态（ctx 会注入 `student_id`） |
| `params_schema` / `result_schema` | JSON Schema 子集（v1 仅作文档与校验参考） |
| `source_hint` | 默认 source 标识（结果未带 source 时兜底） |

## 审核清单（提交前自查）

- [ ] **无网络外传个人数据**：不把学号/成绩等发往任何外部服务；
- [ ] `permission` 如实声明；写操作需说明写入目标与幂等性；
- [ ] 失败路径返回 `{"error": "..."}`，**不抛异常**（框架会兜，但 error 信息更友好）；
- [ ] 返回 dict 必含 `source`（中文标识 + 提供者），数据为估算/缓存时如实标注；
- [ ] 依赖只用项目已有第三方库；新依赖需在投稿说明中给出理由。

## 示例

见 `echo.spec.yaml` + `echo.py`（协议自检用例）。加载与校验逻辑见本包 `__init__.py`；
自检脚本 `scripts/verify_ecosystem.py`。
