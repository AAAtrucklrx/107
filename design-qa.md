# 课表、校园工具与管理后台设计验收

- 验收日期：2026-09-01
- 本地环境：`http://127.0.0.1:8010`
- 参考图：用户本轮提供的课表移动端样例（本地临时附件，未入库）
- 验收范围：课表周视图、校园工具目录与申请、独立管理后台工具审核

## 视觉与响应式

- 课表：在 1440x1000、390x844 下核对；四个大课段、具体起止时间、周一至周日、课程详情和局部横向滚动均清晰可读。
- 校园工具：在 390x844、320x700 下核对；目录、申请入口、搜索、分类和工具信息无遮挡或整页横向溢出。
- 管理后台：在 1440x1000、390x844、320x700 下核对；桌面侧栏、移动导航、申请队列、详情返回路径和审核操作保持完整。
- 320px 管理后台实测：三个审核标签、搜索容器、搜索输入、状态选择器和搜索按钮高度均为 44px；document、body、admin shell、admin main 的 `scrollWidth/clientWidth` 均为 `320/320`。
- 浏览器控制台：0 error，0 warning；失败请求 0。

## 关键链路

- 用户提交工具申请后进入“我的申请”。
- 管理员可搜索和按状态筛选；驳回必须填写原因。
- 审批通过后工具进入同命名空间的全校目录，并生成用户站内通知。
- Demo 审核数据与 Production 永久隔离；普通用户访问 `/admin` 会回到用户端。

## 样例差异

以下差异来自已确认的产品约束，不视为视觉缺陷：

- 一周从周一开始，不提供“全”按钮。
- 暂不处理课程颜色和自定义工具图标。
- Web 移动端课表使用局部横向滚动，保证课程文字可读。
- 保留现有小蜗应用壳、数据来源标识与底部主导航。

## 证据

- `docs/qa/academic-mobile-comparison-final.png`
- `docs/qa/academic-desktop-1440x1000.png`
- `docs/qa/campus-tools-mobile-390x844.png`
- `docs/qa/campus-tools-mobile-320x700-after.png`
- `docs/qa/admin-tools-desktop-1440x1000.png`
- `docs/qa/admin-tools-mobile-390x844.png`
- `docs/qa/admin-tools-mobile-320x700-final.png`

final result: passed
