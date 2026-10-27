# 小蜗前端重构交付记录

日期：2026-10-26。范围：现有 React 前端的设计系统、响应式外壳、会话协调、聊天/学业/我的页面、奶蛙陪伴层。原 Web、Electron 和 Capacitor 继续复用同一套源码。

## 实现结果

| 范围 | 当前行为 | 主要实现 |
| --- | --- | --- |
| 导航 | 手机 `<640px` 固定问答/学业/我的；平板 `640–1024px` 图标 rail + 侧滑菜单；桌面 `>1024px` 常驻 rail | `src/ui/AppScaffold.tsx`、`TabBar.tsx`、`styles/shell.css` |
| 设计系统 | 白/蓝主题、系统字体、浅深两套语义颜色、44px 触控、安全区、两档阴影、200ms 内页面转场 | `src/styles/tokens.css`、`src/ui/*` |
| 首页 | 已连接时显示今日课表、成绩与 GPA、培养方案、下学期推荐、本周日程；匿名保留公开校园问题 | `ChatWorkspace.tsx`、原 `starterPrompts.ts` |
| 聊天 | 用户右侧气泡、助手左侧回答、处理过程折叠、流式光标、多行输入、局部横滑表格、回到最新 | `styles/chat.css`、`RenderedAnswer.tsx` |
| 键盘 | 根据 visualViewport 调整可视高度；同时覆盖 overlay 与 resizes-content；键盘打开时收起手机 Tab 和奶蛙文案 | `hooks/useVisualViewport.ts` |
| 学业 | 手机周课表内部横滑；完整成绩/GPA；培养方案按模块与学期分组 | `AcademicWorkspace.tsx`、`academic.css` |
| 进度 | 仅使用接口明确确认的已修记录；未知进度不显示 0%，不在前端推算 GPA 或学分 | `ProgramView` 及进度回归 |
| 会话与我的 | 一个 SessionProvider；真实身份优先 jw_identity；连接管理、重新连接、断开、退出、外观、关于 | `session/SessionProvider.tsx`、`ProfileWorkspace.tsx` |
| 奶蛙 | 七状态、点击笑 1.5 秒、长按摸头、60 秒睡眠、桌面拖拽回弹、查询/成功/失败反馈、减少动画 | `src/mascot/*` |

旧聊天布局和重复颜色声明已从 `index.css` / `catalog.css` 移除，主应用断点由 scaffold、chat、academic 各自管理。校园服务与管理后台的兼容样式保留。依赖原有 Radix、Lucide、FullCalendar，没有新增大型 UI 框架。

## 会话与业务边界

- `lib/api.ts` 与本次开始前的源码快照完全一致，SHA-256 为 `9005d1edba0e50affe570c4e8250153383bb5aad60199b48a00cfff29371eae2`。Web 仍用 cookie + CSRF，App 仍用已有安全 token；未创建第二套 API 客户端。
- 本次没有修改后端接口、问答/意图/工具逻辑或原生学校认证实现。保留移动 token 迟到请求防护和旧 Electron 桥的单监听兼容。
- 匿名访问学业页展示登录入口，不挂载个人数据工作区。校园服务、演示、管理员入口保留原权限条件。
- 我的页可重新进入教务连接管理。Web 导入/断开后刷新统一身份；原生端沿用现有登录 controller。原生已登录用户离线进入重连页，仍可返回原会话。
- UI 请求取消与卸载已补保护：待创建请求可以停止，旧请求不会更新新页面的奶蛙状态；证据不足和生成超时不会播放成功庆祝。
- 没有提交、推送或部署，也没有操作线上服务器。工作区原有其他未提交改动保留。

## 奶蛙资产

七张 AI 生成素材均为 1024×1024 透明图，提供 PNG 原稿与 WebP。WebP 单张约 53–95KB，合计 443,128 bytes；运行时仅打包 WebP。角色以用户本机参考与同一 idle 母版生成，记录于 [生成说明](../frontend/src/mascot/GENERATION.md) 和 [资产清单](../frontend/src/mascot/assets/manifest.json)。

工具不提供 seed 参数，因此没有伪称固定 seed；以相同母版和形象约束保持一致。奶蛙只保存问候日期、好感度，不读取身份、聊天、token 或学校 cookie。首屏引用 idle，其余状态闲时预加载。

## 截图对比

所有截图使用合成身份、课程、成绩和 SSE，未向真实后台发送个人查询。基线 9 张，重构后 38 张，含三个宽度、浅深主题、宽表格和缩小视口。

| 宽度 | 首页：改造前 | 首页：改造后 | 学业示例 |
| --- | --- | --- | --- |
| 375 | [基线](frontend-rebuild/before-home-375-light.png) | [浅色](frontend-rebuild/after-home-375-light.png) / [深色](frontend-rebuild/after-home-375-dark.png) | [手机周课表](frontend-rebuild/after-schedule-375-light.png) |
| 768 | [基线](frontend-rebuild/before-home-768-light.png) | [浅色](frontend-rebuild/after-home-768-light.png) / [深色](frontend-rebuild/after-home-768-dark.png) | [培养方案](frontend-rebuild/after-program-768-dark.png) |
| 1440 | [基线](frontend-rebuild/before-home-1440-light.png) | [浅色](frontend-rebuild/after-home-1440-light.png) / [深色](frontend-rebuild/after-home-1440-dark.png) | [完整周课表](frontend-rebuild/after-schedule-1440-light.png) |

浏览器检查覆盖可见独立控件 44×44px、页面横溢出、抽屉 Enter/Tab/Escape 与焦点返回、浅深文本对比度、长表格局部滚动。额外交互检查覆盖奶蛙点击/睡眠/忙碌优先/静态模式/真实拖拽、六行输入不被遮挡、普通 Markdown 宽表及长回答回到底部。

这不是完整 WCAG 认证：自动检查没有认证全部 hover 状态、栅格图案或纯图标非文本对比度。真机输入法也不能由浏览器缩小视口替代。

## 验证与复跑

精确结果存于 [响应式验收 JSON](frontend-rebuild/after-verification.json)、[交互验收 JSON](frontend-rebuild/interaction-verification.json)。脚本和合成数据在 `frontend/verification/`；异常接口和外部请求会被拦截，不转发到真实服务。

```bash
cd /home/aaatrucklrx/桌面/小蜗项目/107_repo/frontend
npm ci --cache ../.npm-cache
npm test
npm run build

# 单独终端启动当前源码，随后运行浏览器验收
npm run dev -- --host 127.0.0.1 --port 5175 --strictPort
PLAYWRIGHT_BROWSERS_PATH=/home/aaatrucklrx/.cache/xiaowo-playwright node verification/responsive-check.mjs --phase=after --strict=true
PLAYWRIGHT_BROWSERS_PATH=/home/aaatrucklrx/.cache/xiaowo-playwright node verification/interaction-check.mjs
```

在其他电脑，先执行 `npx playwright install chromium`，按实际路径设置或省略 `PLAYWRIGHT_BROWSERS_PATH`。基线源码快照位于本机忽略目录 `.superpowers/frontend-rebuild-baseline`；截图与合成验收脚本随源码交付。

移动打包沿用原脚本：

```bash
cd /home/aaatrucklrx/桌面/小蜗项目/107_repo/mobile
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ANDROID_HOME=/home/aaatrucklrx/.cache/xiaowo-android/sdk npm run build:debug
```

调试 APK 在 `mobile/artifacts/xiaowo-debug.apk`，当前仍使用 `http://127.0.0.1:8000/api/v1` 和 USB `adb reverse tcp:8000 tcp:8000` 的本机调试方式。正式 HTTPS 地址、签名和发布继续使用原 [移动端说明](../mobile/README.md)，本次没有改变线上地址。

本次 ADB 检测没有已连接设备，因此没有覆盖安装新版 APK，也没有重做真人短信登录或真机软键盘验收。此前移动认证的成功记录保留在 `mobile/ACCEPTANCE.md`，不把旧包验收当作本次新包验收。

## 最终构建记录

- `npm ci --cache ../.npm-cache`：成功，按原 lock 安装。
- `npm test`：23 个文件，138 项通过（重构前为 102 项）。
- `npm run build`：TypeScript 与 Vite 生产构建通过。
- 后端 `pytest tests/web -q`：705 项通过；`verify_security_ui.py`：20/20 通过。既有 ChromaDB 依赖输出一条弃用提示。
- 浏览器严格验收：38 张截图，0 页面溢出、0 小触控、0 已测对比度失败、0 交互失败、0 页面异常；实际奶蛙/长回答补充检查 7/7 通过。
- 两轮独立审查反馈已修复并复查；最终审查的 13 文件/66 项针对性测试、三宽度连接管理流程均通过。
- 完整依赖审计仍报告 8 项既有开发工具依赖告警（3 high、5 moderate），涉及 Vite/Vitest、Tailwind/PostCSS 及间接依赖；生产依赖 `npm audit --omit=dev` 报告 0。升级开发工具是后续维护项，不能把本次构建通过理解为完整安全审计。

- `assembleDebug`：成功，APK **14,223,092 bytes（14.22 MB）**，低于25MB上限。
- 文件：[xiaowo-debug.apk](../mobile/artifacts/xiaowo-debug.apk)。
- SHA-256：`40f78530b4b888032015b374b5dddece489fcaa21d77aa2f9863bc2629619fb2`。
- 已逐文件对比 `mobile/www` 与 APK 内资源，内容一致；构建资源类型检查未发现私密或后端类型文件。
