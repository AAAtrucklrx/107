# 小蛙助手：公网服务器直连部署（2026-10-27）

用户明确选择手机脱离电脑，通过 Wi-Fi / 4G 直接连接现有服务器。目标 API 为 `https://150.158.32.37/api/v1`；不使用公网明文 HTTP，不降低系统证书校验。

## 当前交付状态

- 服务器直连 APK 已构建，公网 HTTPS / 移动认证 / SSE / 撤销端到端测试全部通过。手机可通过 Wi-Fi / 4G 直连 API，不依赖电脑或 USB 转发。
- 用户已在腾讯云放行 TCP 443，公网标准 TLS 验证健康请求返回200。
- 仍使用原有单个 FastAPI 服务；后端8000改为仅监听127.0.0.1，公网由nginx443提供HTTPS。
- 普通HTTP请求307重定向到固定HTTPS地址；HTTP-01路径单独保留。没有使用自签证书、忽略TLS错误或放宽Origin。
- ADB当前无设备，未覆盖安装到手机；真实学校登录、云端教务会话验证和本人课表仍需用户手动验收。

## APK

- [xiaowa-server-direct-debug.apk](../mobile/artifacts/xiaowa-server-direct-debug.apk)
- 实际包内 API 地址：`https://150.158.32.37/api/v1`，mode=`debug`。
- 大小：17,116,166 字节。
- SHA-256：`13f20e83d61dafc12c5b6e674b32eabc145335455ba080b1956c2b81e2a967be`。
- 包名 `cn.xiaowo.app`，显示名“小蛙助手”；签名与此前回环调试包一致，便于覆盖安装保留数据。
- 原回环调试包保留为 `mobile/artifacts/xiaowo-debug-local-loopback.apk`。
- 包内无私钥、环境文件或后端数据库。这是调试签名的直连测试包，不是正式发行签名。
- 切换到新服务器后，原本机后端颁发的登录令牌不适用，需要重新完成学校登录。

## HTTPS 与自动续期

使用 Let's Encrypt 的 shortlived IP 证书，覆盖 `150.158.32.37`，标准系统信任链；无需依赖域名 DNS。官方说明：[Certbot 的 IP 证书支持](https://letsencrypt.org/2026/03/11/shorter-certs-certbot/)。

- Certbot 5.8.0 位于服务器独立 `/opt/certbot/` 虚拟环境，不改变业务 Python 环境。
- HTTP-01 根目录：`/var/www/letsencrypt`，80 上保留 `/.well-known/acme-challenge/` 路由。
- 证书目录：`/etc/letsencrypt/live/150.158.32.37/`；私钥只在服务器。
- 当前证书到期时间：2026-11-03 10:23:08（Asia/Shanghai）。
- `xiaowo-certbot-renew.timer` 每四小时检查，随机延迟不超过15分钟，Persistent=true。
- 续期 deploy hook：`/etc/letsencrypt/renewal-hooks/deploy/10-xiaowo-reload-nginx`，先检查 nginx 配置，再 reload。
- nginx 仅启用 TLS 1.2 / 1.3。APK 保留系统 CA 与 IP SAN 验证。
- 实际执行 `certbot renew --cert-name 150.158.32.37 --dry-run --run-deploy-hooks` 成功，nginx配置检查和reload均完成。

## 后端配置

保留原有 FastAPI 单进程和业务逻辑，保留数据/浏览器会话加密密钥。配置已由实际 WebSettings 校验并应用：

- `XIAOWO_PUBLIC_ORIGIN=https://150.158.32.37`
- `XIAOWO_MOBILE_AUTH_ENABLED=true`
- `XIAOWO_MOBILE_DEBUG_LOOPBACK=false`
- `XIAOWO_MOBILE_AUTH_DB_PATH=/opt/xiaowo/data/mobile_auth_https.db`
- `XIAOWO_MOBILE_TOKEN_SECONDS=43200`
- `XIAOWO_MOBILE_VAULT_KEY` 在服务器随机生成，与其他用途独立；不记录其值。

变更前的 nginx、systemd unit 和环境配置备份位于服务器 `/var/backups/xiaowo-mobile-20261027T111608Z/`，目录700、文件600。候选环境文件也只在此私有目录。公网HTTPS确认后已应用候选配置、重启原服务并立即启用307跳转。原业务数据与加密密钥保留不变，切换前确认活动聊天数为0。后端环境文件和移动数据库权限均为600。

新增systemd drop-in `/etc/systemd/system/xiaowo-web.service.d/90-local-api.conf`，只把原有8000监听地址收紧为127.0.0.1。原competition/demo配置保留，本轮不调整既有浏览器身份策略。

## 验证工具

```sh
node mobile/scripts/check-remote.mjs https://150.158.32.37/api/v1
```

脚本严格验证 TLS，不接受外部 token。覆盖精确 Android 来源预检、错误来源拒绝、匿名 bootstrap / Bearer session、个人数据401、真实 SSE 成功终态、退出与旧 token 失效。它只在内存保留自己创建的匿名令牌，不输出凭据或回答正文。

真实学校密码/SMS登录及个人课表仍须用户在手机完成；自动脚本不代替此验收。联网证据 `web_evidence=false` 是既有问题，不将其等同于移动认证故障。

## 实测结果

| 检查 | 结果 |
| --- | --- |
| 公网可信HTTPS健康接口 | 200 |
| 原HTTP业务入口 | 307，跳转HTTPS |
| 未知ACME挑战路径 | 404，不被业务跳转截获 |
| Android精确Origin预检 | 200，允许https://localhost |
| 错误Origin预检 | 400，无允许来源头 |
| 匿名bootstrap和Bearer session | 200，不设置浏览器cookie |
| 未登录读取个人学业 | 401 |
| 真实公共问题与SSE | 200；37个answer.delta，1个answer.completed |
| 退出与原token再次使用 | logout200，旧token401 |
| API配置构建测试 | 3/3通过 |
| APK构建/包内地址/v2签名 | 通过，与旧包签名相同 |
| 冒烟脚本语法/本地模拟 | 通过；独立审查无待修问题 |
| 证书续期演练与deploy hook | 通过 |
| 服务端ready | 503，仅既有web_evidence=false，其余检查为true |

学校公开无凭据探测在本机和腾讯云均返回相同403，不能由此推断有效登录会话不可用。未注入真实学校凭据，也未尝试绕过学校风控。

## 恢复与后续操作

若需恢复本轮前HTTP服务，使用上述私有备份恢复环境文件与原nginx站点，删除本轮新增的 `90-local-api.conf`，执行 `systemctl daemon-reload`、`systemctl restart xiaowo-web`、`nginx -t` 后再reload nginx。HTTP使用307而非永久跳转，便于回滚。新HTTPS站点与证书续期应一起检查；不要删除或轮换移动加密密钥而保留原移动数据库。

当前直连包可覆盖旧调试包；安装后需要重新完成学校密码/SMS登录，因为原本机服务签发的token与新服务器不通用。正式发行仍需专用release签名和对应发行流程。
