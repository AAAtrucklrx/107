# 构建与交付

## 环境

- Windows 10/11 x64 与 PowerShell 5.1 或 7。
- Node.js 22 或更高版本，仅用于品牌资源生成。
- JDK 21、Android SDK 36、Build Tools 36 和 API 35 模拟器镜像。

工具链脚本默认安装到 `F:\AndroidToolchain`：

```powershell
& .\scripts\install-toolchain.ps1
```

其他磁盘或目录可在同一 PowerShell 会话中覆盖：

```powershell
$env:XIAOWO_ANDROID_TOOLCHAIN_ROOT = 'D:\XiaowoAndroidToolchain'
& .\scripts\install-toolchain.ps1
```

工程的 `android/local.properties`、本地签名材料与所有构建产物均被 Git 忽略。

## 品牌资源

生成 10 套可选图标及候选板：

```powershell
npm install
npm run icons:candidates
```

从当前透明 PNG 生成 Android legacy、round、adaptive foreground 和 monochrome 资源：

```powershell
npm run branding
```

选择候选后可直接指定源图，例如：

```powershell
npm run branding -- --source assets/branding/icon-candidates/04-continuous-path.png
```

## Demo Debug

```powershell
& .\scripts\verify-project.ps1
& .\scripts\build-debug.ps1
```

脚本顺序执行 JUnit、Android Lint、Demo Debug APK 和 AndroidTest APK 构建，输出：

- `artifacts/debug/xiaowo-0.2.0-demo-debug.apk`
- `artifacts/debug/xiaowo-0.2.0-demo-debug.apk.sha256`
- `android/app/build/outputs/apk/androidTest/demo/debug/app-demo-debug-androidTest.apk`

## Demo Release

```powershell
& .\scripts\build-release.ps1
```

首次执行会在 `android/signing/` 创建专用 Demo PKCS12 密钥和随机密码配置。这些文件不会进入 Git；若需要持续更新同一安装渠道，必须私下备份整个目录。

Release 构建会执行 JUnit、Release Lint、R8 压缩和资源收缩，输出：

- `artifacts/release/xiaowo-0.2.0-demo-release.apk`
- `artifacts/release/xiaowo-0.2.0-demo-release.apk.sha256`

该证书只用于比赛/演示包，不是正式生产签名。

## Codex Desktop loopback 兼容

部分隔离子进程会让 Gradle 报 `Unable to establish loopback connection`。这不是工程编译错误，可在普通登录会话中通过临时 Windows 计划任务运行相同构建：

```powershell
& .\scripts\run-scheduled-build.ps1
& .\scripts\run-scheduled-release.ps1
```

任务执行结束后会自动注销；日志位于 `artifacts/build-state/`。

## 设备测试与安装

```powershell
& .\scripts\start-emulator.ps1 -Headless
& .\scripts\run-device-tests.ps1
& .\scripts\install-debug.ps1
```

仪器测试结果写入 `artifacts/test-results/instrumentation.txt`。

## APK 合约校验

```powershell
& .\scripts\verify-apk.ps1 -Variant demoDebug
& .\scripts\verify-apk.ps1 -Variant demoRelease
```

脚本使用 Android 官方 `apkanalyzer`、`apksigner` 与 `zipalign` 检查应用 ID、版本、min/target SDK、权限集合、证书类型、对齐和 SHA-256。

## Production flavor

正式服务地址不写死在仓库中，构建时必须提供可信 HTTPS origin：

```powershell
Set-Location android
.\gradlew.bat assembleProductionRelease -PxiaowoProductionOrigin=https://xiaowo.example.edu.cn
```

地址只能包含协议、主机和可选端口，不能带凭据、路径、查询或片段。Production 网络配置没有任何明文例外。正式发布前还需要配置组织持有的生产签名、部署 HTTPS 更新清单并完成真实认证回归；当前仓库不会自动生成生产证书。
