# 奶家口袋 Implementation Plan

> **For agentic workers:** Use task-scoped implementation and independent review. Steps use checkbox tracking. No commits: repository AGENTS.md overrides skill commit defaults.

**Goal:** 将用户选定的 B 奶家口袋 HTML 设计落实到真实 React / Capacitor App，并重新生成可安装 APK。

**Architecture:** 保留 SessionProvider、学校原生登录、token 和 SSE/API 协议；替换界面结构、角色资产、视觉 token。沿用当前工作目录中的已验证移动端改动，不从旧 HEAD 新建丢失未提交功能的 checkout。

**Tech Stack:** React 19、TypeScript、Vite、Radix、Lucide、Capacitor 7、Android。

**Spec:** 用户 2026-10-26 明确选择已展示的 B 版并要求重新构建；对应工作区 `调研/小蛙助手_手机设计稿/design-brief.md` 与 `b-family.html`。

## Global Constraints

- 正式产品名为小蛙助手；淡黄底、原版奶蛙/奶蛋/奶鼠、问答/学业/我的三导航。
- 首屏 3 个简短功能入口，正文 15–16px，手机标题至多 23px，交互热区至少 44px。
- 角色偏好只影响视觉，不改变认证/会话/真实用户身份。
- 不编造课程或个人数据，不改后端问答/工具逻辑，不操作线上服务器。
- 不提交密钥、cookie 或 .env；不自动登录学校或输入短信码。
- 当前已有未提交工作必须保留；本轮不提交 Git。

## Review Focus

- 改角色、改配色和切换工作区后会话/身份不串用。
- 匿名用户的个人入口继续触发登录引导。
- 长消息、表格、软键盘与小屏不会遮挡输入或底部导航。
- dark / ustc 已有选择仍可读，入口留在“我的”。
- 运行时主形象不再引用旧红嘴图，包体不超过 25MB；真机不可用时如实说明。

### Task 1: 原图角色与偏好
- [x] 复制来源记录和三个原图，扩展 AppearanceProvider character/setCharacter。
- [x] FamilyShortcut + FamilyPicker、Brand 和紧凑 StylePicker。
- [x] 持久化、非法值回退、切换不重挂子树测试。

### Task 2: 问答首页与输入区
- [x] HomeWelcome 改 B 版三角色舞台与短问候。
- [x] ChatWorkspace 使用三个入口和最多一条真实最近会话，工具/模式按需展开。
- [x] 保留 SSE、停止、重试、引用、结构化表格与历史边界，跑聊天测试。

### Task 3: 应用壳与其余页面
- [x] 顶栏只留产品名与角色选择，底部三导航；取消常驻生成角色浮层。
- [x] “我的”、学业及登录页统一字号/间距/表面；真实身份放“我的”。
- [x] 清除运行时旧生成图引用，保留科大蓝设置。
- [x] 更新相关行为测试和文档。

### Task 4: 验证与交付
- [x] 全量前端测试/编译、原生单测；浏览器手机尺寸与交互验收。
- [x] 独立审查后处理实际缺陷。
- [x] build debug + APK 大小/hash 核对；安装时设备断开，已明确记录尚未覆盖安装。
- [x] 截图、构建说明和验收结果交付；不声称未执行的真实认证测试。

验收明细与截图见 [奶家口袋交付记录](../../xiaowa-family-pocket.md)。
