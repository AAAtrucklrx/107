# 小蛙助手 · 奶家口袋交付记录

2026-10-26，按用户选定的 B「奶家口袋」设计，已重构实际 React / Capacitor 界面并生成 Android 调试 APK。原有后端问答、业务工具和认证协议保持兼容。本轮未部署线上服务器，未提交 Git。

## 界面与行为

- 奶油黄底、原版奶蛙/奶蛋/奶鼠等比显示；角色偏好保存在本机，只影响外观。
- 手机顶栏保留产品名与角色入口，底部为问答 / 学业 / 我的。
- 首页仅三个快捷入口与最多一条真实最近会话；工具、历史、来源和核查信息按需展开。
- 手机主标题 21–23px，回答正文 15px、输入文字 16px，主要交互热区至少 44px。
- “我的”集中显示真实身份、教务连接与退出入口；保留科大蓝和深浅色。
- 切换页面或角色保留输入草稿及进行中的 SSE；身份改变时重新挂载聊天，避免上一身份的数据混用。隐藏页面不会弹出聊天弹层。
- Android 图标和启动画面换成原版奶蛙；旧生成红嘴图不再用作运行时主形象。

沿用 React 19 + TypeScript + Vite + Radix / Lucide + Capacitor 7。无需更换整个技术栈即可实现选定的排版，同时保留已验证的学校登录与流式问答能力。

## 构建产物

- 文件：[xiaowo-debug.apk](../mobile/artifacts/xiaowo-debug.apk)
- 大小：17,116,166 字节（17.12 MB，低于 25 MB）
- SHA-256：`c3308ed617835b8627fc0d6ba2a2fe2fdb6e6447b0081186f0d91d9488590680`
- 构建：在 `mobile/` 执行 `ANDROID_HOME=$HOME/.cache/xiaowo-android/sdk npm run build:debug`，含网页编译、资源同步和 `assembleDebug`。
- 技术包 ID `cn.xiaowo.app` 与产物文件名保持兼容，应用名为“小蛙助手”。

当前包连接 `http://127.0.0.1:8000/api/v1`，供 USB `adb reverse` 本机调试；手机调试服务由 `mobile/scripts/dev_server.py` 启动，数据和密钥隔离在被忽略的 `mobile/.runtime/`。发行版仍要求显式配置 HTTPS。详细打包、签名及安装步骤见 [mobile/README.md](../mobile/README.md)。

## 验证记录

| 验证 | 结果 |
| --- | --- |
| 前端 Vitest | 25 个测试文件、153 项通过 |
| 后端 `pytest tests/web -q` | 705 项通过；1 个 Chroma 弃用提示 |
| Android 原生单测 | 12 项通过 |
| 移动构建配置测试 | 3 项通过 |
| TypeScript / Vite / Gradle debug | 通过 |
| 独立代码审查 | 已关闭切页、隐藏弹层、身份隔离和平板导航问题 |
| 浏览器真实后端 | 匿名询问个人课表收到先登录提示；历史回答与 SSE 可显示 |
| 手机 320 / 390px 与平板 768px | 无文档横向溢出，输入区和导航可见；390px 可见控件最小高度 44px |
| 交互 | 角色切换、草稿保留、工具面板、历史、学业登录边界及深色模式已检查 |
| 本轮真机安装 | 构建后设备断开；`adb install -r` 返回无设备，尚未更新手机 |
| 本轮真实学校登录 / 本人课表 | 未重复操作，不以自动测试替代真人验收 |

此前会话已有真实学校登录、本人课表、重启保留身份和退出后要求登录的用户验收。本轮保留相应流程；这不代表新包已经完成同样的真机验收。

## 截图与素材

- [390px 首页](xiaowa-family-pocket/home-390.jpg)
- [320px 首页](xiaowa-family-pocket/home-320.jpg)
- [390px 个人页](xiaowa-family-pocket/profile-390.jpg)
- [390px 真实匿名回答](xiaowa-family-pocket/chat-390.jpg)
- [布局检查记录](xiaowa-family-pocket/layout-checks.json)

三个原图来自奶家图鉴，大笑 GIF 来自奈 hub；保留原图文件、来源和校验信息。详见 [SOURCES.md](../frontend/src/appearance/assets/SOURCES.md)。图片许可与代码许可不同，本记录不作版权授权声明。
