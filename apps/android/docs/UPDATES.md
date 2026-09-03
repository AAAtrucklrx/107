# 更新预设

## Web 内容

应用启动时加载 flavor 编译进包内的固定 origin。Demo 是 `http://114.214.241.119:8850`，因此服务端发布新的 Web 页面后无需重新打包 APK。

服务器是完整信任边界。任何 Web 发布都必须保留访问控制、Cookie/CSRF 校验、Demo 与生产数据隔离和可回滚部署。

## Android 原生包

配置位于 `android/app/src/main/res/raw/update_config.json`，当前保持 `enabled: false`。启用前必须：

1. 将正式服务迁移到可信 HTTPS。
2. 在 HTTPS 地址发布 `/mobile/android/update.json`，结构参考 `docs/examples/update.json`。
3. 提供 HTTPS APK 下载地址。
4. 递增 `versionCode`，填写展示版本与真实 APK SHA-256。
5. 使用稳定的正式签名证书构建，并验证旧版本可升级安装。
6. 把配置中的 `manifestUrl` 改为 HTTPS，再将 `enabled` 设为 `true`。

启用后，成功检查间隔默认 24 小时；失败后 15 分钟可重试。客户端拒绝重定向与非 JSON 响应，清单上限 128 KiB。

发现新版本时只展示版本、说明和哈希摘要，由用户选择是否打开 HTTPS 下载页。APK 下载和安装仍由浏览器与系统安装器接管；客户端不申请静默安装能力。
