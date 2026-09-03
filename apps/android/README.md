# 小蜗 Android

小蜗 Web 用户端与演示管理端的 Android 客户端。当前版本已从 Capacitor 远程 Bridge 壳重构为受控原生 WebView：业务仍由 107 的 Web 应用提供，Android 只负责安全导航、生命周期、下载、剪贴板、错误恢复和系统集成。

## 当前交付

| 项目 | 值 |
| --- | --- |
| 应用 ID | `cn.edu.ustc.xiaowo` |
| 版本 | `0.2.0-demo` (`versionCode 2`) |
| Demo 服务 | `http://114.214.241.119:8850` |
| 最低系统 | Android 8.0 / API 26 |
| 编译与目标 SDK | API 36 |
| 设备验证 | API 35 Google APIs x86_64 模拟器 |
| Debug APK | `artifacts/debug/xiaowo-0.2.0-demo-debug.apk` |
| Release APK | `artifacts/release/xiaowo-0.2.0-demo-release.apk` |

Demo 服务仍是明文 HTTP。应用会始终显示安全警示，不得输入真实 CAS 密码、个人敏感信息或生产数据。正式版本的构建配置强制使用 HTTPS。

## 已解决的壳层问题

- 移除 Capacitor Bridge，远程页面不能调用通用原生插件。
- 只拦截主框架导航，iframe、API、SSE 和页面资源请求不再被误当作外链。
- 精确限制 Demo 同源；外部 HTTPS 使用 Custom Tabs，危险协议默认阻止。
- 支持 `target="_blank"` / `window.open`、Cookie 持久化、旋转状态恢复和返回历史。
- 为不安全 HTTP 页面提供受限剪贴板与 Blob 下载兼容，并且每次都由用户确认。
- 普通 URL 下载交给系统 DownloadManager，携带当前同源 Cookie 和 User-Agent。
- 页面错误、SSL 错误、安全浏览命中和 WebView 渲染进程退出均有明确恢复路径。
- Demo 与 Production 使用独立 flavor；Production 在配置与网络策略上都拒绝明文 HTTP。

## 快速验证

```powershell
npm install
npm run icons:candidates
npm run branding
& .\scripts\verify-project.ps1
& .\scripts\build-debug.ps1
& .\scripts\build-release.ps1
& .\scripts\start-emulator.ps1 -Headless
& .\scripts\run-device-tests.ps1
& .\scripts\verify-apk.ps1 -Variant demoDebug
& .\scripts\verify-apk.ps1 -Variant demoRelease
```

Codex Desktop 子进程遇到已知 Java NIO loopback 限制时，使用普通 Windows 计划任务入口：

```powershell
& .\scripts\run-scheduled-build.ps1
& .\scripts\run-scheduled-release.ps1
```

详细流程见 [构建说明](docs/BUILD.md)、[安全边界](docs/SECURITY.md)、[测试记录](docs/TESTING.md)、[更新预设](docs/UPDATES.md) 和 [图标候选](docs/ICON-CANDIDATES.md)。

## 工程结构

- `android/`：Android Studio / Gradle 工程与 Java WebView 壳。
- `scripts/`：品牌资源、工具链、构建、签名、设备测试与 APK 校验脚本。
- `assets/branding/`：当前品牌源图、10 套候选 SVG/PNG 和生成哈希清单。
- `docs/`：构建、安全、测试、更新与图标选择说明。
- `artifacts/`：本地 APK、日志和截图；不进入 Git。
