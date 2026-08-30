---
version: 1
slug: "frontend-src-app-tsx"
primary_target: "frontend/src/App.tsx"
related_targets: ["frontend/src/catalog.css","frontend/src/components/AppShell.tsx","frontend/src/workspaces/ChatWorkspace.tsx","frontend/src/workspaces/AcademicWorkspace.tsx","frontend/src/workspaces/CampusWorkspace.tsx","frontend/src/workspaces/ReviewWorkspace.tsx"]
---

# React 四工作区

- **模式：** Operate。首屏直接完成问答、学业查看、校园入口或知识审核，不承担营销任务。
- **受众与任务：** 科大学生快速获取可核验回答与个人学业数据；审核者逐块核验公开资料并管理 generation。
- **主要动作：** 对话提问是公共主动作；登录后按当前工作区完成学业查看、校园跳转或审核决定。
- **内容证据：** 身份、数据源、演示隔离、检索阶段、行内引用、来源等级、TTL 和审核三态必须比装饰更显眼。
- **约束：** 保留全部路由、API、权限和身份隔离；匿名历史不并入账号；推荐软硬条件与独立冲突检查不变；演示审核永不进入生产索引。
- **方向：** 冷色数字编目台。深色索引脊组织四工作区，冷白连续工作面承载任务；蓝色只标主操作，青色只标联网与证据。
- **记忆点：** 桌面端稳定的索引脊与连续数据带；移动端转为顶部字标和底部工作区导航，任务内容保持原生单列流。
- **未决事项：** 正式 CAS 域名和生产个人区仍待可信 HTTPS 来源确定；不影响本轮演示接口与视觉系统。
