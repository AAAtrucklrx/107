# 奶蛙素材与陪伴层

生成日期：2026-10-26。七张静态图使用内置 `image_gen.imagegen` 逐状态独立生成，透明背景；未调用 API/CLI，也未使用 SVG 或 CSS 绘制角色。大笑默认使用用户要求的网上传播版本 GIF，来源与使用范围见下文。

## 参考与一致性

用户提供的角色参考：

- `/home/aaatrucklrx/图片/炫酷壁纸/奶家食堂_经典奶蛙修订版.png`：以左前方黄色角色为身份来源。
- `/home/aaatrucklrx/图片/炫酷壁纸/奶蛙参考图/大笑奶蛙_视频缩略图.jpg`
- `/home/aaatrucklrx/图片/炫酷壁纸/奶蛙参考图/大笑奶蛙_传播样本.jpg`
- `/home/aaatrucklrx/图片/炫酷壁纸/奶蛙参考图/奶蛙大笑_动态样本.gif`：160×160、149 帧、每帧 30ms；本轮按用户要求原样复制为 `assets/laugh-original.gif`，未裁切、调色、去水印或改帧。

先生成 idle，首版光晕明显且为竖幅，退回；第二次要求正方形、去光晕、嘴略张且不伸舌，得到目前母版。随后其余六张均只使用这张 idle 母版作为 reference，锁定黄色高梨形、奶油腹、凸起绿白眼和小黑瞳、橄榄褐手脚及软 3D 风格。七张均已目视检查，不使用绿色或圆矮角色。

**内置工具没有 seed 参数，未设置或伪称固定 seed。** 一致性来自同一母版参考和明确的不变特征。工具输出的最终七张均为 1254×1254 RGBA；本地用 Pillow 等比缩到 1024×1024，保留 alpha，生成 PNG 原稿与 WebP（quality=84、method=6）。未改变角色形状或绘制细节。应用引用七张 WebP 和一张原样 GIF，PNG 用于交付与后续编辑，不进入运行时 bundle。

## 网上大笑动图

- 传播页面：[TapTap 奶蛙动态样本](https://www.taptap.cn/moment/856513735462749195)。这是可追溯的转载来源，不能据此认定原作者或授权。
- [原始 GIF 地址](https://img2-tc.tapimg.com/moment/etag/FuW0x94jNjXVDTsRa-bySQjS0OS-_20261006131747.gif/_tap_ugc.gif)。应用使用本地副本，不在运行时请求第三方图片服务器。
- 字节数：1,022,956；SHA-256：`4ae14eb556e6e90162f8dc6a917d74d5cab12df972e20b8eebb1181e4544f342`，与用户本地参考文件逐字节一致。
- 用途：本地预览包的大笑反应。尚未核实原作者及再分发许可，不声称它是自制或已获商用授权；公开发布前需完成素材授权或替换。来源记录另见 `assets/laugh-original.source.json`。
- GIF 本身约 4.47 秒，但界面的反应时长仍为 1.5 秒；不叠加 CSS 大笑摇摆。系统开启减少动态效果时使用静态 `laugh.webp`；GIF 加载失败也回退到该静态图，仍按原有计时返回 idle/think。

## 最终提示词集

共有部分（除 idle 外，每次以最终 idle 为图片参考）：

> Create a transparent 1:1 square full-body 3D app sticker of the EXACT SAME yellow 奶蛙 in the reference. Keep identical elongated tall pear-shaped yellow body, large cream belly, dark olive-brown fingers/toes, protruding pale green round eyes with TINY black pupils, same rendering/proportions. One character, full body and feet inside generous square canvas margins. Transparent alpha background, clean antialiased edges, no aura/glow/floor/groundshadow/text/watermark/extra character. Never green body, squat body, or huge black pupils. Same soft overhead lighting.

每状态动作约束：

| 文件 | 提示词中的动作与表情 |
| --- | --- |
| `idle` | Exact SQUARE 1:1 1024×1024 transparent PNG. Same character from initial reference. Character occupies only 75% of canvas height. Small relaxed open mouth, tongue NOT extended, eyes glancing sideways, relaxed arms at sides. Remove luminous glow/halo and floor shadows; alpha=0 beyond clean antialiased edges. Do not crop. |
| `laugh` | Head/body leaning backward in uncontrollable laughter, mouth extremely wide open showing tongue, eyes squeezed to curved slits, BOTH long olive hands clutching belly, knees slightly bent. Big explosive laugh, not polite smile. |
| `think` | Slight head tilt, one long olive index finger resting at chin, other arm resting along side, eyes looking upward, tiny pursed thinking mouth. |
| `sleep` | Standing sleepily leaning head to shoulder, both eyes closed as relaxed curved lines, mouth barely open, long arms relaxed at belly, gentle dozing pose. Three tiny floating pale-blue sleep bubbles, no alphabetic Z letters or other text. |
| `celebrate` | BOTH long arms raised high in triumphant cheer, open dark olive palms, head tipped backward laughing very widely with tongue visible, eyes squeezed into curved slits, a few loose blue and yellow ribbon pieces above shoulders. |
| `surprised` | Round O-shaped open mouth, eyes wide with small pupils looking forward, one long hand by cheek and other hand held out uncertainly, shoulders slightly raised. Friendly confused surprise, not horror. |
| `eat` | One long olive hand holds a small white rice bowl at belly level, the other uses dark brown chopsticks holding a tiny bite near open mouth, cheeks gently puffed chewing, cheerful hungry expression. |

`assets/manifest.json` 记录最终规格、字节数、SHA-256 和生成源文件名。七张透明 WebP 均小于 200,000 bytes。

## 接入与行为

```tsx
import { MascotLayer } from "../mascot/MascotLayer";
import { emitMascot } from "../mascot/events";

// 外壳挂载一次，不接触 Session/API。
<MascotLayer />
emitMascot("think");
emitMascot("success");
emitMascot("error", { message: "先连接教务，再一起查" });
emitMascot("idle"); // 停止/取消
emitMascot("eat");  // 生活类前端彩蛋
```

- 默认 idle；点击 laugh 1.5 秒；有未完成问题时笑完回 think。
- success → celebrate 3 秒；error → surprised 3 秒；eat → 3 秒；新的请求/终态取消旧反应定时器。
- 任意键盘/指针/滚动活动重置 60 秒睡眠；think 不睡眠；再次活动唤醒。
- 长按 500ms 摸头，压扁回弹 180ms，好感度 +1、最多 100；长按不额外触发点击。移动超过 8px 取消长按；桌面可拖拽，松手 180ms 回角落，移动端保留垂直滚动。
- 当天首次闲置打开，500ms 后按本地时间问候；不会覆盖先到的问答状态。只在 localStorage 存问候日期和好感度，不存账号、聊天、token 或学校 cookie。
- 手机与平板在外壳正常流中；手机行高 60px，键盘打开后 44px 并收起文案。桌面右下角，底部留出 `--composer-height + 24px`（最小 168px）；全局面板仍在更高层。
- 原生 button 支持键盘激活；图片为空 alt，不重复朗读；气泡 polite status。`prefers-reduced-motion` 完全关闭动作，仅切换静态状态图。
- 首屏只有 idle 图被 `<img>` 引用；其余在 requestIdleCallback（3 秒 timeout，旧环境 setTimeout 1.2 秒）预加载，减少动态效果时跳过 GIF 预加载。生成原稿不会作为页面预加载资源。

## 本模块验证

`npm test -- --run src/mascot/Mascot.test.tsx` 覆盖真实 DOM 行为和假计时器，不 mock 状态机：点击时长、原版 GIF/静态切换与加载失败回退、60 秒活跃重置、查询状态优先级、终态/取消定时器、摸头/拖拽/移动滑动区分、reduced-motion、每日问候竞争和食堂彩蛋。

视觉验收仍由三档宽度的集成页面截图检查位置和遮挡；浏览器假计时器测试不能替代手机真键盘验证。
