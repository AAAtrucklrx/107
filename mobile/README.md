# 小蛙助手 Android App

客户端现名“小蛙助手”，默认采用用户选定的奶家口袋界面，保留科大蓝与深浅色切换。技术命名、包 ID 和产物文件名保留兼容。见 [奶家口袋交付记录与截图](../docs/xiaowa-family-pocket.md)。

当前奶家口袋版的 Android 图标和启动画面使用[奶家图鉴](https://xn--6kr28k0gz4eq33b1t7a.com/naijia/)的原版奶蛙 PNG：`android/app/src/main/res/drawable-nodpi/naiwa_original.png` 与设计稿素材 `naiwa-main.png` 逐字节一致，没有重绘、改色或重新编码。黄色背景与等比留白由原生 XML 绘制；传统图标、自适应图标和启动画面共用 `drawable/xiaowa_mark.xml`。旧生成图暂留磁盘，但已不被资源入口引用。详见[素材来源记录](../frontend/src/appearance/assets/SOURCES.md)；原图版权归各自权利方，代码许可不代表图片许可。

同一 React 前端与 Capacitor 7.6.9 工程。APK 内只有网页资源、Android 容器和原生登录桥；不包含 Python、模型、向量库或学校凭据。iOS 尚未实现原生桥，不能把 Android 验证结果视作 iOS 支持。

## 服务器直连测试包（2026-10-27）

已部署可信 IP HTTPS：`https://150.158.32.37/api/v1`。`artifacts/xiaowa-server-direct-debug.apk` 实际使用此地址，支持手机 Wi-Fi / 4G 直连，无需 `adb reverse`。保留原调试签名，可覆盖旧包；切换服务器后需重新登录学校账号。构建时显式设置：

```sh
XIAOWO_MOBILE_API_BASE=https://150.158.32.37/api/v1 npm run build:debug
```

服务端鉴权、SSE和退出撤销已通过；真实学校登录/个人课表仍待手机验收。详见 [服务器直连部署记录](../docs/mobile-server-direct-2026-10-27.md)。此包为调试签名测试包，正式发布仍按下文签名流程办理。

## 本地运行

需要 Node 22+、JDK 21、Android SDK 35 / Build Tools 35.0.0、USB 调试设备。Android 最低版本为 9（API 28），用于学校登录进程的独立 WebView 数据目录；不依赖新版 WebView 的站点删除功能。

在仓库根目录执行：

```sh
npm ci --prefix frontend
npm ci --prefix mobile
export ANDROID_HOME="$HOME/.cache/xiaowo-android/sdk" # 或 Android Studio 的 SDK 路径
export PATH="$ANDROID_HOME/platform-tools:$PATH"
.venv-local/bin/python mobile/scripts/dev_server.py
```

启动器复用本地 `.env` 中的模型服务配置，但不显示或复制密钥；生成分离的移动会话/数据/签名密钥，并将测试数据库和检索索引存放在被忽略的 `mobile/.runtime/` 中。不会继承原有青春科大个人会话或快照。只监听本机 8000，不操作线上服务器。没有模型配置时，问答按现有后端逻辑降级。

公共 `data/course_data.db` 通过 SQLite 只读连接制作本地快照，供原有课程/评课工具读取；FAQ 的独立索引使用现有 `knowledge/data` 公开种子。原业务库中的个人历史、成绩与会话不复制，浏览器学校凭据导入在此启动器中禁用。

另开终端，在仓库根目录执行：

```sh
export ANDROID_HOME="$HOME/.cache/xiaowo-android/sdk"
npm run build:debug --prefix mobile
bash mobile/scripts/install-debug.sh
```

脚本依次构建 React、复制 `frontend/dist` 到 `mobile/www`、`cap sync android`、`./gradlew assembleDebug`，并检查 APK 不超过 **25,000,000 字节**。产物在 `mobile/artifacts/xiaowo-debug.apk`，附 SHA-256 校验文件。`www`、Gradle 输出、SDK、运行密钥均不提交。

安装脚本执行 `adb reverse tcp:8000 tcp:8000`，因此手机的 `127.0.0.1:8000` 指向电脑后端。多设备时指定 `ANDROID_SERIAL`。USB 线连接后需在开发者选项开启 USB 调试，并在手机上允许电脑调试；仅有充电或文件传输连接并不代表 ADB 可用。

调试包默认 API 为 `http://127.0.0.1:8000/api/v1`；HTTP 只在 debug 的回环链路获准。学校网站始终 HTTPS。请勿将调试包当成发行包使用。

## 学校登录与会话

启动先清理学校临时浏览数据，再检查 Keystore 中保存的小蛙助手令牌。没有令牌时显示登录页，不自动打开学校认证。用户也可选择匿名使用，匿名令牌仅存在进程内。

“用学校账号登录”打开独立进程中的原生 WebView，使用学校原始 UA，不注入表单脚本、不获取密码/短信码；用户自己完成学校的密码和短信验证。按用户最新确认，应用不再设置导航域名、路径或端口白名单，WebView按学校安全网关与二次认证流程自行跳转；网页新窗口在同一登录WebView内打开。系统TLS校验、HTTPS传输和文件访问限制继续生效。导航不授予Cookie读取权限：Cookie读取仍严格限于 `id.ustc.edu.cn` 和 `jw.ustc.edu.cn`，登录落地只认精确教务域。落地教务后，JS通过 `@capacitor/core` 的 `CapacitorCookies.getCookies` 调用受限的原生适配器，以应用内部IPC在内存中读取必要Cookie；只有正在进行的登录能读取指定学校域，且必须含 `SOURCEID_TGC`。

原生适配器替换同名 Capacitor Cookie 插件，是因为 Android 核心插件的 `getCookies` 从主 WebView 的 `document.cookie` 读取，不能满足独立登录 WebView 的 HttpOnly Cookie 读取。学校 WebView 没有 Capacitor bridge。内置 Cookie 写接口已禁用，避免默认插件写入或记录凭据。

Cookie 只在内存中交给后端验证，通过 PKCE 绑定的单次 code 兑换 43 字符随机 Bearer。默认最长12小时，学校会话可以更早失效。服务器保存 token hash、加密身份和加密 Cookie；Android 用 Keystore AES-256-GCM 加密小蛙助手令牌，SharedPreferences 只保存密文。学校 Cookie 不进入该存储。

成功显示真实姓名2秒，等待学校 WebView 清理完成后进入现有主界面。恢复登录会验证教务状态；网络故障保留令牌供重试，确定失效才清除。所有 App API 与 SSE 都使用 Authorization Bearer，不依赖浏览器会话 Cookie。

系统 WebView 在登录期间可能写入临时 Cookie；这是已确认接受的系统限制。学校页面使用独立的 `school_login` 数据目录。成功、取消、退出及下次启动均终止学校进程，确认死亡后删除其专用数据与缓存目录，保留 localhost 下的应用历史与偏好。Android 标准目录是 `app_webview_school_login`；本次真机发现华为 WebView 还使用 `app_hws_webview_school_login` 保存实际 Cookie、IndexedDB 与 ServiceWorker，清理必须覆盖这一固定目录，并保留无后缀的主应用目录。清理失败会阻止新学校登录，并允许匿名使用或恢复已有小蛙助手会话；重试清理成功后才重新开放登录。需在目标系统上实际检查残留，不能仅凭 API 回调声称存储设备上从未有过数据。原生单测和仪器测试只使用合成值，不包含真实凭据。

## 后端正式配置

正式部署需单独准备可信 HTTPS 域名、反向代理与私有持久目录。此次实现不更改线上服务。新增配置默认关闭：

| 变量 | 含义 |
|---|---|
| `XIAOWO_MOBILE_AUTH_ENABLED=true` | 启用移动认证 |
| `XIAOWO_MOBILE_VAULT_KEY` | 独立随机密钥，至少32字节，不与其他密钥复用 |
| `XIAOWO_MOBILE_AUTH_DB_PATH` | 独立私有 SQLite 文件；不得与业务/审核库重合 |
| `XIAOWO_MOBILE_TOKEN_SECONDS=43200` | 令牌绝对有效期，60–43200秒 |
| `XIAOWO_DATA_KEY` | 原有聊天历史等数据的独立加密密钥 |
| `XIAOWO_SESSION_SECRET` | 原有 Web 会话签名密钥 |
| `XIAOWO_PUBLIC_ORIGIN` | 正式 HTTPS 服务来源 |
| `XIAOWO_MOBILE_DEBUG_LOOPBACK=false` | 正式环境保持关闭；仅 development 可启用回环 HTTP |

移动来源仅接受精确的 `https://localhost` / `capacitor://localhost`，不得放宽任意 Origin。Origin 是浏览器来源约束，不能当作设备认证或防逆向秘密；令牌权限必须通过学校会话验证获得。客户端包中没有静态服务端密钥。

接口：`POST /api/v1/auth/mobile/bootstrap`、`authorize`、`exchange`；既有 `GET /auth/session` 返回身份，`DELETE /auth/sso_cookie` 和 `POST /auth/logout` 撤销移动会话。code 有效60秒且只能兑换一次。令牌不得放入 URL、日志或 localStorage。离线退出清理设备并提示服务器撤销未确认；服务器会话最迟在绝对期限失效，不承诺断网后已远程撤销。

## 正式打包与签名

```sh
XIAOWO_MOBILE_API_BASE=https://YOUR-APP-DOMAIN/api/v1 npm run build:release --prefix mobile
```

发行构建必须显式指定 HTTPS；直接调用 Gradle release 也会检查网页构建元数据，拒绝 debug 资源。默认产出 `xiaowo-release-unsigned.apk`，不生成或提交发行签名密钥。由发布者在安全环境中签名，例如用 Android SDK 的 `zipalign` 与 `apksigner`（口令在交互提示输入，不写命令行或仓库）：

```sh
"$ANDROID_HOME/build-tools/35.0.0/zipalign" -p -f 4 mobile/artifacts/xiaowo-release-unsigned.apk /tmp/xiaowo-aligned.apk
"$ANDROID_HOME/build-tools/35.0.0/apksigner" sign --ks /secure/path/xiaowo-release.jks --out mobile/artifacts/xiaowo-release.apk /tmp/xiaowo-aligned.apk
"$ANDROID_HOME/build-tools/35.0.0/apksigner" verify --verbose mobile/artifacts/xiaowo-release.apk
```

保管同一签名密钥以支持后续升级。发布前更新 versionCode/versionName、填写隐私政策中的运营主体与模型提供方、在正式域名和目标设备完成验收。

## 测试与验收

```sh
npm test --prefix frontend
node --test mobile/scripts/build-config.test.mjs
PYTHON_DOTENV_DISABLED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv-local/bin/python -m pytest tests/web mobile/scripts/test_dev_server.py -q
cd mobile/android
./gradlew :app:testDebugUnitTest :app:assembleDebugAndroidTest :app:lintDebug
./gradlew :app:connectedDebugAndroidTest
```

设备仪器测试会写入/清理合成令牌和学校 Cookie，应在登录真实账号**之前**运行，不要对正在使用的会话执行。任何被跳过的设备测试都不能计入验收通过。

CI 实际入口为仓库根 `.github/workflows/mobile.yml`（GitHub 不执行嵌套的 `mobile/.github/workflows`）。构建、前端/后端/原生单测及仪器测试编译通过后上传调试 APK。学校真实密码与短信登录不在 CI 自动化。干净源码不含部署用课程库和教务快照，因此既有16项业务数据测试按原有条件跳过，并输出跳过原因；移动鉴权、线程隔离和本地启动器测试不依赖这些私有资产。

真机验收：手动学校登录 → 欢迎2秒 → 主界面真实姓名/学号/已连接教务 → 询问本人本学期课表 → 杀进程重开保持登录 → 退出后个人问题提示登录 → 断网/后端不可用显示重试 → 检查应用私有文件与日志中学校凭据残留。扫描仅输出命中与否和文件位置，不输出真实 Cookie、密码、短信码或令牌。

## iOS 接口与未来迁移

共享接口在 `src/login/controller.ts`、`src/login/token.ts`；iOS 后续实现同名 `SchoolLogin`、受限 `CapacitorCookies`、Keychain `SecureToken`，保持清理完成才能进入主界面的语义。当前只有 Android 原生工程，不提供未实现的安全存储回退。

学校若提供面向本应用的 OAuth/OIDC 客户端注册，则改用 AppAuth + PKCE，移除学校 Cookie 读取与托管。现阶段不尝试把自定义学校 SSO 改造成 App 的 CAS callback。

隐私草案见 [PRIVACY.md](PRIVACY.md)，设计与实施记录见 `../docs/superpowers/`。

实现依据：[Android WebView 独立进程数据目录](https://developer.android.com/reference/android/webkit/WebView#setDataDirectorySuffix(java.lang.String))、[Capacitor Cookies](https://capacitorjs.com/docs/apis/cookies)、[Capacitor 安全公告 GHSA-rvm3-566m-v7fv](https://github.com/ionic-team/capacitor/security/advisories/GHSA-rvm3-566m-v7fv)。
