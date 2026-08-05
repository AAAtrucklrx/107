# 2026-08-05 silent-fallback-fabricated-data

## 目标

finding: `silent-fallback-fabricated-data`（High）。
- 教务接口失效链增加可归因诊断（日志携带关键入参）与明确的来源标识
- 响应不再静默混用模拟数据，查询语义保持不变

## 改动文件

- `tools/course_tools.py`：
  - 10 个降级/模拟分支返回值增加自然语言 `message` 字段（课表/成绩/GPA/选课=本地缓存、空教室/考试=模拟数据、课程搜索/学期/培养方案=本地数据），明确"教务接口暂时不可用…仅供参考"；locked 分支沿用原 `_LOGIN_MSG`
  - 降级日志补关键入参：课表 `(student_id, week, day/date)`、空教室 `(building, time)`、成绩 `(student_id, course_name, semester)`、GPA `(student_id, semester)`、考试 `(student_id, course_name)`、选课 `(student_id, semester)`、课程搜索 `(keyword)`、培养方案 `(student_id, module_id)`
- `agents/executor.py`：
  - 新增 `SOURCE_LABELS`：real→"数据来源：教务系统实时数据"、fallback→"数据来源：本地缓存/模拟数据，仅供参考"、locked→"数据来源：需登录教务系统后获取"
  - `_format_single_result`：来源标注前置，通用格式化跳过 source/message 字段（避免 "source: fallback" 生硬输出）
  - `_fallback_synthesize`：同样输出来源标注并跳过 source/message
  - `SYNTHESIZE_PROMPT` 新增第 6 条硬性要求：source 非 real 时回答开头必须注明数据来源，不得把降级数据当作实时数据呈现

## 验证

- `py -m py_compile tools/course_tools.py agents/executor.py`：通过
- 降级路径实测（未登录态）：
  - `query_exam` → source=fallback + "⚠️ 教务接口暂时不可用，以下为考试模拟数据，仅供参考"：通过
  - `query_grade` → source=fallback + 本地缓存成绩消息：通过
  - `find_empty_room` → source=real（catalog API 当前可用，无 message，符合预期）：通过
- 执行器映射实测：fallback 结果标注前置、real 结果标注"教务系统实时数据"、source/message 不再出现在通用格式化中：通过

## 遗留问题

- 多步计划的来源披露依赖 LLM 遵守 SYNTHESIZE_PROMPT 第 6 条（已降级路径 _fallback_synthesize 兜底，但中间态无法强制）
- 登录后的 real 路径（成绩/课表）待修复 2 端到端实测确认解析契约
