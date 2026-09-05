# Spec — 今日推荐弹窗（替代顶部条）

- 日期：2026-09-05（演示阶段）
- 状态：已与用户确认（grill-requirements 六维度）

## 1. Goal
把聊天页的 TodayStrip 顶部条移除，改为**打开页面即弹出的"今日卡"弹窗**，展示今日课程与今日活动（简短精炼）。

## 2. Boundary
- 改：`frontend/src/workspaces/ChatWorkspace.tsx`（删 TodayStrip、新增弹窗组件）、`frontend/src/catalog.css`（today-strip 样式清理 + 弹窗样式）
- 不碰：后端接口（复用 `/academic/schedule`、`/campus/activities?time_window=今日`）、审核/QA 链、其他工作区
- TodayStrip 相关样式 `.today-strip` 清理；`todaysCourses` 仅在弹窗组件内保留

## 3. Acceptance criteria
1. ChatWorkspace 打开时（登录用户 personal_academic=true）**每次**自动弹出今日卡；匿名不弹
2. 卡片含：标题"今日 · <学期> · 第X周"；**今日课程**（时间+课程+地点，最多 ~5 条；无课显示"今日无课"）；**今日活动**（📌 名称 · 开始时间(HH:mm) · 地点 · 一句话理由，最多 3 条；无活动不显示该区块）
3. 关闭：卡片右上 X 或底部"关闭"与遮罩点击均可关闭；**不留重新打开入口**
4. 时间文本直透后台真实值（不换算/不改写）；今日活动窗口=北京时区（复用现有 API）
5. 弹窗可滚动（内容多时）且适配移动端；玻璃卡风格与其他 dialog 一致
6. 前端构建 `npm run build` 通过；`tsc --noEmit` 零错误；现有 pytest 212/212 不回归

## 4. Failure modes
- schedule/activities 任一接口失败 → 对应区块隐藏/显示"今日无课"，弹窗其余部分正常；两者全失败仍显示标题+关闭（不崩溃）
- 未登录 → 不弹窗（与 TodayStrip 原条件一致）
- 活动 0 条 → 只显示课程区（无空活动标题）

## 5. Priorities
- 优先：弹窗功能正确 + 关闭可靠；样式美观次之；移动端适配第三（演示以桌面为主）
- 可裁剪：活动理由行（若空格不足仅名称+时间）

## 6. Non-goals
- **不做**：弹窗后手动重开入口（用户已确认不留）
- **不做**：每日一次/记忆频率（演示阶段每次弹）
- **不做**：日程提醒推送/定时弹出
- **不做**：顶部条任何形式保留
