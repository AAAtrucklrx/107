# 小蛙助手 · 双风格交付

2026-10-26。按用户最新要求替换“小蜗”客户端品牌，新增奶蛙黄 / 科大蓝。此前的学校认证、个人学业和问答协议继续复用。

## 已实现

- 默认奶蛙黄：奶油底色、黄色主要操作、首页奶蛙、互动陪伴。
- 科大蓝：蓝白校园界面，隐藏陪伴角色，为内容保留更多空间。是本项目的视觉选择，不表示科大官方产品。
- 顶部调色盘可即时切换；“我的”页有两张风格预览卡。风格与浅/深色模式独立，选择保存在本机。
- 两套风格共享同一 React 树、会话与问答组件，切换不会重新初始化学校登录或清空聊天草稿。
- 网页标题、品牌、登录提示、回答署名、辅助操作和 Android 名称/图标改为“小蛙助手”。旧服务端的 `placeholder: true` 占位事件使用当前品牌文案，首段增量到达后正常替换。
- Android 旧版、自适应图标与启动页使用现有奶蛙图像。包 ID `cn.xiaowo.app`、协议、token 存储键保持兼容。

## 真实大笑素材

随包带入本机已有、来源核对一致的 [TapTap 网传 GIF](https://www.taptap.cn/moment/856513735462749195)。160×160，149 帧、4.47 秒；文件原样复制，没有裁剪、抠图或重新生成。点击时按已有反应时长播放 1.5 秒；减少动态效果或图片加载失败使用静态图。不在运行中请求第三方图床。

GIF 是转载素材，原作者和再分发许可未核实，不能把它标为已获商业授权。其余七状态静态图是上一轮生成的素材。完整来源、hash 和两类素材的区别见 [mascot/GENERATION.md](../frontend/src/mascot/GENERATION.md)。

## 技术栈判断

实际 lock / 本机环境：React 19.2.8、TypeScript 7.0.2、Vite 8.2.2、Radix、Tailwind 3.4.17、Capacitor 7.6.9；后端 FastAPI 0.161.1 + LangGraph 1.2.9。

本轮保留现有技术底座并改造展示层。换框架不会自动带来更好的视觉；已经验证的自定义学校认证、Keystore 和跨端 UI 是有价值的资产。

| 路线 | 对此项目的实际影响 | 结论 |
| --- | --- | --- |
| React + Capacitor | 同一份前端覆盖 Web / Android，继续使用已验证原生桥 | 本轮采用 |
| React Native + Expo | 原生视图；DOM、Radix、日历和 Markdown 布局需要移植，自定义桥需 development build | 未来若以原生移动为唯一重心再评估 |
| Flutter | 绘制控制力强，但 Dart UI / 状态层及平台通道需要重做；体积重新测量 | 本轮没有足够收益支撑整栈重写 |

官方依据：[Capacitor](https://capacitorjs.com/docs)、[自定义 Android 原生代码](https://capacitorjs.com/docs/android/custom-code)、[React Native 组件](https://reactnative.dev/docs/intro-react-native-components)、[Expo development builds](https://docs.expo.dev/develop/development-builds/introduction/)、[Flutter 架构](https://docs.flutter.dev/resources/architectural-overview)、[Flutter 平台通道](https://docs.flutter.dev/platform-integration/platform-channels)。路线选择是结合项目约束作出的工程判断。

当前维护改进空间仍包括历史 CSS、较大的 ChatWorkspace、依赖声明中的 `latest`；本轮没有把框架主版本迁移混入品牌修改。原有开发依赖安全告警仍见 [前次交付记录](frontend-rebuild.md)。

## 验证与交付

- `npm test`：24 文件、142 项通过。
- `npm run build`：TypeScript / Vite 构建通过；移动 debug 构建也通过。
- 两套风格各 38 张截图，375 / 768 / 1440 宽度、浅/深色：页面横向溢出、44px 触控、已测文字与 token 对比度、浏览器错误、未知接口均为 0 项失败。属于脚本覆盖范围内的检查，不是完整无障碍认证。
- 原有 7 项真实浏览器交互通过：点击、sleep、忙碌优先、减少动画、拖动回弹、多行输入不遮挡、宽表和长回答滚动。
- 切换风格专项：320 / 375 / 768 / 1440 × 浅深色，8 组 / 32 项断言通过。真实菜单和我的页切换保持草稿、答复与身份；离线切换正常、刷新保持偏好，不增加 session/config 请求。独立复核没有发现本轮功能缺陷。
- Android 单测 12 项通过；lint 无错误，11 条现有平台兼容/未使用资源等警告，未把告警当作零问题。
- 原版 GIF 解码、动态偏好实时切换、加载失败静态降级经过合成接口浏览器检查。全部测试使用合成身份，无个人数据访问。
- APK 的 29 个网页资源与 `mobile/www` 逐字节一致；内置 GIF hash 与源文件一致。未发现私钥、环境、数据库、Python 后端资源。

APK：[mobile/artifacts/xiaowo-debug.apk](../mobile/artifacts/xiaowo-debug.apk)，文件名保留构建脚本兼容，安装名称为“小蛙助手”。**14,152,042 字节（14.15MB）**。

SHA-256：`d104833cbfd07b44f3a0ff2aa8a24acb4a39c7395477a5c4bcc8102abad743fa`。

仍是本地调试配置 `http://127.0.0.1:8000/api/v1`，需 USB `adb reverse`。ADB 当前无设备，未安装本版、未复验真机学校登录/键盘。没有更改线上服务器。

## 预览与复跑

- [奶蛙黄 · 手机](xiaowa-styles/frog/after-home-375-light.png)
- [科大蓝 · 手机](xiaowa-styles/ustc/after-home-375-light.png)
- [奶蛙黄 · 桌面](xiaowa-styles/frog/after-home-1440-light.png)
- [奶蛙黄 · 深色我的页](xiaowa-styles/frog/after-profile-375-dark.png)

测试脚本位于 [frontend/verification](../frontend/verification/README.md)，两套风格报告与截图保存在 `docs/xiaowa-styles/`。偏好存储已同步到 [隐私政策草案](../mobile/PRIVACY.md)。
