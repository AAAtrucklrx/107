# 小蜗前端重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将已有小蜗前端改造成手机、平板、桌面共用的设计系统，完成奶蛙陪伴交互，保留真实学校登录与业务能力。

**Architecture:** 沿用 React/Vite、现有 Radix/Tailwind 与中央 API 传输模块；提取 SessionProvider，外壳负责三档导航，工作区负责内容。状态与数据的业务处理不重写，奶蛙通过纯前端事件订阅反馈。

**Tech Stack:** React、TypeScript、CSS 变量、现有 Radix、Lucide、FullCalendar、Vitest、Playwright；内置 imagegen 生成透明角色资产。

**Spec:** `/home/aaatrucklrx/桌面/小蜗项目/调研/前端重构_完整提示词.md`，用户已明确“直接开始实施”。

## Global Constraints

- 手机 `<640px`，平板 `640–1024px`，桌面 `>1024px`；截图宽度375/768/1440。
- 手机固定问答/学业/我的 Tab；校园服务保留桌面导航及我的页入口，原 `/campus` 路由继续有效。
- 触控目标至少44px、无hover-only操作、安全区、键盘避让、深色模式、WCAG AA；一般转场≤200ms。
- 不改后端接口、问答/工具算法、`lib/api.ts` 鉴权传输、原生移动登录与令牌协议；保留取消/过期/迟到请求防护。
- 不新增Ant/MUI等重型框架；依赖已有Radix原语，样式用中央tokens和职责清晰的组件CSS。
- 奶蛙按本地准确参考图：黄色高梨形、奶油肚皮、橄榄褐修长手脚、凸起绿白眼与小黑瞳；不以SVG/CSS代画角色。
- 七状态idle/laugh/think/sleep/celebrate/surprised/eat；点击laugh1.5秒、60秒无操作sleep、reduced-motion静态、移动不遮挡导航/输入。
- 不提交、不推送、不操作线上、不触碰.env/个人数据/密钥；当前未提交的移动实现与其他文件改动均保留。

## Decisions / rulings

- 直接实施的用户指令覆盖设计/计划反复审批；先记录具体实现选择后继续执行。
- 在当前`feat/xiaowo-android`工作区继续，保留所有未提交资产；单独保存前端源码基线用于前后截图和差异审查。新worktree不包含已验收的未提交移动实现，故不切换。
- 样式优先用已有CSS变量与Radix；不为重构新增框架。旧管理/校园功能保留兼容样式，主外壳与聊天/学业迁至统一断点。
- 现有`lib/api.ts`已统一Web cookie/CSRF与App Bearer，通过Provider协调身份；不再添加第二套HTTP实现。
- 内置imagegen无seed参数；用同一角色参考和母版保证一致性，在交付记录中如实说明。
- 文档有laugh1.5秒/图示3秒冲突：点击按明确验收1.5秒，成功庆祝3秒；短交互结束回到当前任务的think或idle。
- 匿名学业页展示登录空态；不发送个人接口请求。已有登录页启动行为保持，未授权更改默认登录策略。
- 并行任务只写各自文件，root负责共享样式入口/ChatWorkspace/构建配置；测试互用临时状态，禁止覆盖其他任务代码。

## Review Focus

1. 旧账户请求迟到、退出/取消、StrictMode与Electron订阅不得覆盖当前身份（任务2原移动race回归）。
2. 手机键盘、长Markdown表格和长会话不得遮挡输入与Tab或把页面横向撑开（任务5/6浏览器验证）。
3. 未知培养方案进度不能显示0%或虚构完成状态，仍标注缓存/通用参考（任务4合成数据测试）。
4. 奶蛙拖拽不能触发点击，睡眠不能覆盖运行中think，离开/取消不会留下计时器（任务3状态及交互测试）。
5. 小屏抽屉的焦点/关闭、深色对比度、触控尺寸与reduced-motion在真实浏览器验证（任务1/2/6）。

### Task 1: 设计原语、tokens和基线（root）

**Files:** `frontend/src/styles/tokens.css`, `frontend/src/ui/{Button,Input,Card,Page,Sheet,Skeleton,Empty}.tsx`, `frontend/src/ui/ui.css`, `frontend/src/main.tsx`, `frontend/src/catalog.css`, `frontend/verification/*`。

**Interfaces:** 保持原有语义变量`--canvas/--surface/--surface-raised/--surface-muted/--ink/--muted/--line/--primary`等；UI原语为可组合可访问组件。Sheet使用Radix Dialog焦点管理。AppScaffold/TabBar由任务2拥有。

- [x] 保存源码基线、运行当前Vitest，生成三个宽度的改造前截图（合成API数据，不使用个人凭据）。
- [x] 建立颜色、字号、间距、圆角、阴影、转场和断点tokens；兼容现有组件变量，系统字体栈。
- [x] 实现原语并测试Sheet键盘关闭/焦点返回等真实交互。
- [x] 收敛主应用旧布局/media补丁，保留管理与校园旧功能可用。

### Task 2: SessionProvider、三形态外壳、我的页（shell_session）

**Files:** `frontend/src/session/*`, `frontend/src/App.tsx`, `frontend/src/components/{AppShell,LoginPage,MobileLoginPage,SsoImportPanel}.tsx`, `frontend/src/ui/{AppScaffold,TabBar}.tsx`, `frontend/src/styles/shell.css`, `frontend/src/workspaces/ProfileWorkspace.tsx`, 相关App/Login/Shell测试；`types.ts`仅Workspace新增profile。

**Interfaces:** Provider整体平移现有会话生命周期，导出`SessionProvider`/`useSession`，由App保留路由。Scaffold负责viewport类别（phone/tablet/desktop）与导航。ProfileWorkspace通过Provider执行登录/退出/主题操作。MascotLayer由AppShell在非管理布局内单次挂载，资产任务提供组件。

- [x] 新增匿名学业登录空态和我的页导航测试；原移动race/退出/401测试保持保护。
- [x] 提取Provider，保持controller稳定、token快照current检查、disposed处理和原API函数。
- [x] 用统一会话驱动SSO面板；按实际Electron桥契约清理订阅，防止重复会话状态。
- [x] 手机三Tab、平板折叠rail、桌面常驻rail；campus/管理保留可达性，身份优先jw_identity。
- [x] 我的页提供身份、连接状态、主题、登录/退出、关于，保留演示/管理功能。
- [x] 跑拥有文件及移动race/API相关测试；交付任务说明供独立审查。

### Task 3: 奶蛙素材、状态机与交互（mascot）

**Files:** `frontend/src/mascot/*`（含资产、CSS、测试、生成提示词记录）；不编辑ChatWorkspace/App/main/package。

**Interfaces:** `MascotLayer`组件无必需props；`emitMascot(type: 'think'|'success'|'error'|'idle'|'eat', options?: {message?:string})`纯前端事件函数供聊天调用。桌面角落预留输入框空间，手机在页面内正常流展示。

- [x] inspect本地参考，用内置imagegen逐状态生成七张透明1024PNG并检查一致性；转WebP，每张≤200KB。
- [x] 用有意义的假计时器测试点击1.5秒/无操作60秒、think优先、终态/取消、reduced-motion与长按拖拽区分。
- [x] 实现七状态与台词，成功celebrate、失败surprised、每天首次问候、按当地昼夜文案；默认只加载idle，闲时加载其余。
- [x] 桌面可拖拽且松手≤200ms回到安全角落；移动不浮盖输入或导航；键盘可点击且装饰不抢焦点。
- [x] 记录生成来源/提示词、压缩规格与未支持seed的事实，跑本模块测试。

### Task 4: 学业页面响应式（academic_ui）

**Files:** `frontend/src/workspaces/AcademicWorkspace.tsx`, `.test.tsx`, `academic.css`，必要内部只读进度类型置本组件内。

**Interfaces:** 原session/onAsk props、原API/课程映射/周次/冲突逻辑保持；使用任务1语义tokens。独立样式作用域`academic-workspace`，手机周网格内部水平滚动。

- [x] 添加进度未知不显示0%、有效值按返回显示的回归测试。
- [x] 手机课表可横滑、合理文字与触控，保留今日/周次/时间线/冲突/详情；平板桌面完整周网格。
- [x] GPA与学分摘要、手机成绩列表与桌面表格，培养方案按模块分组。
- [x] 只在`taken_courses_known===true`且数值有效时展示真实进度；保持来源/限制说明。
- [x] 跑学业测试并提供改动说明；不改业务层。

### Task 5: 首页与聊天（root）

**Files:** `frontend/src/workspaces/ChatWorkspace.tsx`, `.test.tsx`, `frontend/src/components/LaunchTile.tsx`, `frontend/src/styles/chat.css`, `frontend/src/hooks/useVisualViewport.ts`, 必要新首页展示组件。

**Interfaces:** 保留SSE处理/历史/反馈/表格/取消逻辑；在提交和终态调用任务3的`emitMascot`。会话数据从任务2传入不改变权限。已有五个personalStarterPrompts为首页入口。

- [x] 五个个人入口手机紧凑列表、桌面网格；今日课表置顶，公开与匿名入口继续诚实说明权限。
- [x] 全屏消息流、用户右助手左、可读结构化卡、流式光标、可折叠工具过程。
- [x] 输入多行自动增高、吸底、发送/停止、visualViewport键盘避让；长回答滚动保留用户位置。
- [x] 通过原聊天/安全渲染测试，加长内容/viewport与奶蛙事件真实行为回归。

### Task 6: 集成、截图、审查与打包（root +独立review）

**Files:** `frontend/verification/*`, `docs/frontend-rebuild.md`, `docs/frontend-rebuild/*`, 本计划状态记录；构建输出忽略目录。

- [x] 每个任务进行规格与代码审查，修复阻断项后整合。
- [x] 375/768/1440亮暗截图，浏览器断言无页面横向溢出，抽屉焦点、44px触控、对比度、长表格、键盘与奶蛙遮挡检查。
- [x] `npm test --prefix frontend`、`npm run build --prefix frontend`、移动debug构建全通过；按AGENTS完成相关整体检查，不触线上。
- [x] 构建≤25MB APK；ADB当前无设备，未安装新包；保留既有Keystore/身份与手动认证流程（真机键盘待设备）。
- [x] 写`docs/frontend-rebuild.md`含设计取舍、迁移范围、三档前后截图、测试证据与未验项；不自提交。
