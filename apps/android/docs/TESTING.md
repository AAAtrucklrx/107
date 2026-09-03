# 验证记录

验证日期：2026-09-04（Asia/Shanghai）。

## 自动构建

- `testDemoDebugUnitTest`：通过。
- `lintDemoDebug`：0 errors；仅报告 Gradle 8.14.5 可用的版本提示。
- `assembleDemoDebug`：通过。
- `assembleDemoDebugAndroidTest`：通过。
- `lintDemoRelease`：通过。
- `assembleDemoRelease`：通过，R8 与资源收缩启用。
- `verify-project.ps1`：项目安全合约通过。
- Debug/Release `verify-apk.ps1`：应用 ID、版本、SDK、权限、签名、对齐、SHA-256 全部通过。

## 单元测试覆盖

- Demo BuildConfig 与版本合约。
- 下载文件名、MIME、Base64 大小限制与空内容拒绝。
- 更新检查的成功间隔与失败重试窗口。

## API 35 仪器测试

Google APIs x86_64 模拟器上共 10 项，全部通过：

- MainActivity 创建受限 WebView并显示 Demo 安全边界。
- 精确 origin、端口、首页与显示 authority 判断。
- 同源、外部 HTTPS、系统协议和危险协议的顶层路由。
- Production origin 拒绝凭据、路径与明文 HTTP。
- Blob 元数据预检与空内容拒绝。
- 更新清单 HTTPS、版本与 SHA-256 校验。

原始输出：`artifacts/test-results/instrumentation.txt`。

## 真实页面烟测

- Demo 首页加载成功，无原生错误层。
- 首页与“校园服务”SPA 页面切换成功。
- Android 返回键从校园服务返回首页，Activity 未重建。
- 校外 HTTPS 卡片启动 Chrome/Custom Tab；返回后恢复 `cn.edu.ustc.xiaowo/.MainActivity`。
- 日志未出现应用崩溃或 AndroidRuntime 异常。

本地截图位于 `artifacts/screenshots/`，不进入 Git。

## 尚未宣称完成的生产验证

- 真实 CAS 登录与生产 Cookie 回调。
- 真实生产 HTTPS 域名与证书链。
- 生产签名升级安装。
- OEM 厂商系统上的后台回收与下载行为。

这些项目需要正式基础设施和发布证书，不应由 Demo 结果替代。
