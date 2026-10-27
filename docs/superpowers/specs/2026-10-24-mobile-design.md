# 小蜗 Android App 设计

日期：2026-10-24。依据：用户完整移动端任务及本会话两次澄清，现有工作树为实现基线。

## 目标与边界

Capacitor 7 Android 应用复用现有 React，构建产物复制到 `mobile/www`，不包含 Python、模型或数据库。APK ≤25 MB。交付 Android 工程、可重复构建、CI、测试、隐私草案和验收记录，iOS 保留同构插件接口。可选离线课表和通知本次不实现。

保留现有未提交登录改动，不提交 Git，不部署或修改线上服务器。问答、意图、工具业务算法不变；允许修复认证上下文的线程传播和身份绑定。

## 已确认的取舍

用户已明确接受系统 WebView 登录期间临时持久化 Cookie：应用不自行保存明文，完成、取消、退出及下次启动清理并检查残留。因此本版本**不承诺登录期间或崩溃时 Cookie 从未落盘**。

用户选择本地调试 `127.0.0.1:8000`，设备经 `adb reverse tcp:8000 tcp:8000` 接入。HTTP 例外仅限显式 debug 构建和回环地址；release 强制可信 HTTPS，禁止将真实凭据发送至公网 HTTP。服务器域名以后申请。本轮使用本地后端，无需连接线上。

方案比较：外部浏览器不能读取学校 Cookie；普通 CapacitorCookies Android 实现仅读主 WebView 的 document.cookie，不能完成学校 HttpOnly 读取；采用独立无桥学校 WebView + 白名单原生 CookieManager 适配，在 JavaScript 侧保留 `@capacitor/core` 的 CapacitorCookies API。锁定安全修订版 Capacitor 7.6.9 并测试同名插件覆盖行为。将来学校有 OAuth/OIDC 时替换为 AppAuth + PKCE。

## 目录与所有权

- `mobile/` 放在 Git 主仓库内，便于和后端同版本交付。
- `mobile/android` 原生工程；`mobile/src/login` 登录控制器、桥接口和 token 存储。
- `frontend/src/lib` API/SSE 传输选择；原有 Web 和 Electron 保持 cookie 路径。
- 真正生效的 CI 位于仓库根 `.github/workflows/mobile.yml`；`mobile/.github/workflows` 提供说明，因为 GitHub 不执行嵌套 workflow。
- 新移动认证默认关闭、密文库独立；本地调试使用隔离数据目录，不迁移/清空现有业务数据。

## 服务端协议

移动 origin 严格限定 `https://localhost`（Android）和 `capacitor://localhost`（未来 iOS）；它是来源约束，不被当作设备身份证明。身份只通过学校会话服务端校验取得。

1. `POST /api/v1/auth/mobile/bootstrap`：创建受限匿名 bearer，用于现有匿名业务 API。返回 `{access_token, token_type:"Bearer", expires_in}`。匿名 token 只在进程内保存。
2. `POST /api/v1/auth/mobile/authorize`：JSON `{cookies, code_challenge, code_challenge_method:"S256"}`。严格必要学校域/名称和载荷上限；校验真实教务会话及身份，返回 `{code, expires_in:60}`，不写浏览器 Cookie。
3. `POST /api/v1/auth/mobile/exchange`：JSON `{code, code_verifier}`。原子单次兑换，返回 `{access_token, token_type:"Bearer", expires_in}`；前端随后 GET `/auth/session`。
4. bearer 为 32 随机字节（base64url，43 字符），服务器只存 hash；已验证学校凭据 AES-GCM 密文托管，密钥独立配置。token 最长12小时，学校会话失效仍需重新人工登录，无无限刷新。
5. `GET /auth/session` 保持现有字段和 capabilities，并返回真实 `jw_identity`/`jw_session`。有效 bearer 分支不签发 Cookie，不要求 CSRF；无效 bearer 必须401，不能回退到 Cookie。浏览器 Origin/CSRF 规则保持。
6. `DELETE /auth/sso_cookie` 和 `POST /auth/logout` 对 bearer 撤销当前设备认证、学校密文和运行任务，重复请求可安全处理。无网络退出立即清本地凭据并明确告知服务器撤销尚未确认；不伪称已远程注销，服务端有绝对有效期。
7. API/SSE 使用 Authorization header，绝不把凭据放 URL。只对明确 origin 配 CORS，不用通配。写请求保留 JSON-only、来源校验、限流和 no-store。

新增 bearer 接线复用 Principal 和现有存储的 owner 规则。两个线程入口传递认证快照，每工具独立请求客户端，避免共享可变 requests.Session。身份从学校查询验证后绑定，不能信任前端提交学号。动态授权守卫随上下文传播，在后续线程入口与学校 HTTP 发送前检查撤销/过期；不能回滚已经发出的网络请求。

## Android 原生契约

学校 URL 固定为 `https://id.ustc.edu.cn/cas/login?service=https%3A%2F%2Fjw.ustc.edu.cn%2Fucas-sso%2Flogin`。

独立学校 WebView 不注入 Capacitor/JavaScriptInterface，不读取表单、不代填、不改 UA。2026-10-25实测初始302链路使用 `analytics.ustc.edu.cn/.isg/gateway/`；用户最终明确要求“跳转不要有任何限制”，因此移除全部应用层导航域名/路径/端口白名单，允许网页自动新窗口并由同一WebView承接。Cookie读取策略独立，其他域Cookie不能导入。保留SSL错误取消、系统HTTPS传输、禁文件访问/混合内容/调试日志和发行版WebView调试。落地判据必须解析URL后校验host精确等于 `jw.ustc.edu.cn`，不能用子串。

目标机为 Android API31、华为 WebView114。采用 `:school` 独立进程，在首次 WebView 调用前设置 `school_login` 数据目录后缀。学校 Activity/Service 不导出，必要 Cookie 仅经应用内部 Messenger 在内存传递。关闭通过 Binder 死亡确认进程退出，之后删除固定专用 profile/cache 并检查不存在；启动先处理崩溃遗留，禁止删除活跃进程目录或默认主 App profile。最低 API28，不以新版 `DELETE_BROWSING_DATA` 作为兼容前提。

原生插件 `SchoolLogin`：
- `open(): Promise<void>` 打开登录页；事件 `landed`、`cancelled`、`retry`。
- `showStatus({state:"loading"|"error"|"success",message:string}): Promise<void>`；错误停留并提供重试，成功显示姓名。
- `close(): Promise<void>` 关闭学校窗口与进程，等待死亡确认和专用目录删除后 resolve。
- `cleanup(): Promise<void>` 启动和退出时使用。

原生 `CapacitorCookies` 覆盖实现仅允许 `https://id.ustc.edu.cn` 与 `https://jw.ustc.edu.cn`（含具体path），只在学校登录窗口活跃时读必要项，包括 HttpOnly。JS必须分别读两个域，存在非空 SOURCEID_TGC 才提交。

原生 `SecureToken`：`get(): Promise<{value:string|null}>`、`set({value:string}):Promise<void>`、`remove():Promise<void>`。Android Keystore 持有 AES-GCM 密钥，token 密文写私有存储；禁备份，不回退 localStorage，密钥失效时清理并重新登录。iOS 同名接口以后用 Keychain实现，当前不能假称 iOS 已交付。

## 前端行为

启动先 native cleanup 和 Keystore token 恢复；验证有效 token后进应用；无 token进入登录页，可选匿名体验，不自动打开学校登录。

登录生成 PKCE verifier仅存内存，监听 landed后用 CapacitorCookies 读取必要项，authorize/exchange，再安全保存 token及获取会话。成功显示“✅ 欢迎，姓名”2秒后清学校数据并回应用。导入失败留在登录页重试。取消与迟到响应通过会话序号隔离，取消后不得存入迟到token。

移动应用 API/SSE 使用 bearer；跨 origin fetch保留流式能力，不启用会把流变成整块响应的 CapacitorHttp补丁。请求设置超时、AbortSignal，错误转友好中文。401清本地token并引导重新登录；普通网络错误不得误清登录态。

使用 @capacitor/app 处理返回键与仅允许固定路由的 deep link；@capacitor/network 提示离线。主界面显示真实姓名/学号/教务连接状态，顶部按 Asia/Shanghai 显示时段问候与日期。Web/Electron路径不自动变成移动登录页。

## 构建与验收

JDK21、SDK35、minSdk28、Gradle8.11.1/AGP8.7.2，锁版本和 lockfile。debug通过 adb reverse；release脚本拒绝HTTP、不带测试后门和学校凭据，不包含本地.env，CI检查大小≤25MB并上传APK。

自动测试：单次code并发兑换/重放/过期/PKCE、token撤销/重启恢复/错密钥、来源和CSRF回归、两用户线程隔离、SSE认证与撤销、登录状态机取消/失败/重试、URL白名单、Keystore/清理原生仪器测试。

设备人工验收：用户亲自输入密码和短信；欢迎页2秒；真实身份和课表；杀进程恢复；退出后个人问题需登录；WebView数据与logcat仅做敏感值存在性检测，不输出实际值。没有现场执行的项目必须明确未验证。

## 版本裁定（依赖审计后）

实现统一锁定 Capacitor **7.6.9**，取代初选的7.4.3，原因是官方GHSA-rvm3-566m-v7fv已披露7.4.3存在原生代理导航漏洞。自定义Cookie接口需在7.6.9重新核对；不通过关闭HTTP插件来假装修复该漏洞。

## 一手依据

- https://github.com/ionic-team/capacitor/blob/7.6.9/android/capacitor/src/main/java/com/getcapacitor/plugin/CapacitorCookies.java
- https://developer.android.com/reference/android/webkit/CookieSyncManager
- https://capacitorjs.com/docs/v7/updating/7-0
- https://developer.android.com/privacy-and-security/keystore
- https://github.com/ionic-team/capacitor/security/advisories/GHSA-rvm3-566m-v7fv
- https://developer.android.com/reference/android/webkit/WebView#setDataDirectorySuffix(java.lang.String)
