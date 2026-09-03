# 安全边界

## Demo 风险声明

`0.2.0-demo` 固定连接 `http://114.214.241.119:8850`。HTTP 链路可能被监听或篡改，因此应用顶部永久显示“演示环境 · 不安全”提示。不得在该包内输入真实 CAS 密码、长期凭据或个人敏感信息。

Production flavor 同时通过 Gradle 配置校验、Manifest cleartext 开关和 Network Security Config 强制 HTTPS。

## 远程页面能力

主界面是标准 Android WebView，不包含 Capacitor、Cordova 或 JavaScript Interface。远程页面只能使用浏览器自身能力，无法直接访问通用文件、相机、麦克风、定位、设备信息或任意原生插件。

WebView 配置包括：

- 禁止 `file:` 与 Content Provider 访问。
- 禁止 file URL 跨域与通用访问。
- 禁止混合内容、定位和 Web 权限申请。
- 开启 Safe Browsing；SSL 错误一律取消。
- 接受第一方 Cookie，拒绝第三方 Cookie，并在暂停时落盘。
- 仅 Debug 包启用 WebView 调试。

## 导航与弹窗

- 精确同源主框架页面留在主 WebView，协议、主机与端口必须完全一致。
- 子框架导航不由 Android 壳拦截，避免破坏 iframe、资源、API 和 SSE。
- 外部 HTTPS 主框架链接通过 AndroidX Custom Tabs 打开。
- `mailto:`、`tel:`、`sms:`、`geo:` 与 `market:` 交给明确的系统应用。
- 其他协议，包括额外 HTTP、`file:`、`content:`、`javascript:` 与伪造后缀域名，默认阻止。
- `target="_blank"` 与 `window.open` 使用无脚本临时 WebView 只解析目标地址，随后立即销毁。

Custom Tab 与主 WebView 不共享 JavaScript Bridge。返回外链后，主应用 Activity 与页面状态保持不变。

## Cookie、CSRF 与认证

同源业务请求继续由 WebView CookieStore 处理，因此 107 的 Cookie、Origin 与 CSRF 逻辑保持浏览器语义。Android 壳不会读取、改写或绕过角色权限。

Demo 身份与演示管理端仍由服务器控制。真实 CAS 登录不应在明文 Demo 环境中使用；HTTPS Production 上线前必须单独验证认证跳转、回调 origin 与会话共享策略。

## 下载与剪贴板

HTTP 页面通常不具备 Secure Context。为了保持现有页面功能，壳只向精确同源页面注入两项窄兼容：

- 剪贴板写入最大 1,000,000 字符，必须弹出原生确认框。
- Blob 下载最大 8 MiB，先检查元数据与编码长度，再在后台解码；必须弹出原生确认框。

Android 10+ Blob 文件保存到 `Downloads/小蜗`，无需共享存储权限。普通 URL 下载也必须确认，并交给 DownloadManager；只允许主同源 HTTP 或任意 HTTPS，携带当前 Cookie 与 User-Agent。

## 权限与备份

源码只声明 `android.permission.INTERNET`。最终 APK 还包含 AndroidX 自动定义的应用内 `signature` 级动态广播保护权限，它不是系统数据权限，也不会弹出授权对话框。

应用备份与设备迁移均明确排除文件、数据库、SharedPreferences 和外部文件，防止会话数据被系统备份复制。

## 更新

原生更新检查默认关闭。启用后要求 HTTPS 清单和 HTTPS 下载地址、拒绝重定向、限制清单为 128 KiB，并且仅在用户确认后用 Custom Tab 打开下载页。不静默下载、不静默安装，也不申请“安装未知应用”权限。

检查失败 15 分钟后可重试；只有成功获取并解析清单后才记录成功时间，避免一次失败压制全天更新检查。
