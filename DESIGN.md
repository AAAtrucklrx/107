---
name: 小蜗科大学术工作台
description: 安静、精确、可核验的科大校园智能工作台
colors:
  primary: "#034ea1"
  primary-hover: "#023a79"
  primary-soft: "#e2ecf7"
  retrieval: "#087680"
  retrieval-soft: "#dceff0"
  canvas: "#eef2f8"
  surface: "#f7f9fc"
  surface-raised: "#ffffff"
  surface-muted: "#e9eef5"
  ink: "#1c2430"
  muted: "#566672"
  faint: "#5f6e7a"
  line: "#d5dde6"
  line-strong: "#aebbc4"
  rail: "#17242d"
  rail-raised: "#21323d"
  rail-ink: "#eef4f7"
  glass: "rgb(255 255 255 / 0.55)"
  glass-strong: "rgb(255 255 255 / 0.85)"
  glass-weak: "rgb(255 255 255 / 0.38)"
  glass-solid: "#f3f6fb"
  gblur-light: "blur(14px) saturate(170%)"
  gblur-dark: "blur(16px) saturate(150%)"
  dark-canvas: "#0a0f16"
  dark-surface: "#10161f"
  dark-surface-raised: "#161e2a"
  dark-ink: "#e8eef6"
  dark-muted: "#a8b6bf"
  dark-line: "#2b3a44"
  dark-primary: "#5b9be0"
  dark-retrieval: "#4fc8ce"
typography:
  headline:
    fontFamily: '"Noto Sans SC Variable", "Microsoft YaHei UI", sans-serif'
    fontSize: "22px"
    fontWeight: 760
    lineHeight: 1.25
    letterSpacing: "0"
  title:
    fontFamily: '"Noto Sans SC Variable", "Microsoft YaHei UI", sans-serif'
    fontSize: "16px"
    fontWeight: 730
    lineHeight: 1.4
    letterSpacing: "0"
  body:
    fontFamily: '"Noto Sans SC Variable", "Microsoft YaHei UI", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.7
    letterSpacing: "0"
  label:
    fontFamily: '"Noto Sans SC Variable", "Microsoft YaHei UI", sans-serif'
    fontSize: "12px"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "0"
  mono:
    fontFamily: '"Cascadia Code", "SFMono-Regular", Consolas, monospace'
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "0"
rounded:
  none: "0px"
  status: "3px"
  control: "5px"
  surface: "6px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "20px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface-raised}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  input:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  status:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.status}"
    padding: "4px 7px"
  catalog-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "13px 4px"
---

# Design System: 小蜗科大学术工作台

## Overview

**Creative North Star: "晨雾 × 夜航 · 液态玻璃"**

小蜗是一张持续工作的科大学术工作台：浅色主题（晨雾）在暖白画布上铺低饱和彩色光斑，侧栏、顶栏、对话输入与浮层以半透明玻璃悬浮其上；深色主题（夜航）用三层 charcoal 灰阶与暗色玻璃承载同一结构。界面安静、精确、信息密度适中，玻璃只用于关键层，正文与数据保持高可读实底。品牌存在于定制字标、玻璃质感、连续数据带和可信状态中，而不是营销式构图。

**Key Characteristics:**

- 关键层液态玻璃（侧栏/顶栏/输入区/浮层/移动底栏），内容主体保持可读实底
- 零 filter 彩斑背景（纯 CSS radial-gradient 双层 crossfade，明暗双份）
- 精选启动方块、分类目录、数据带和账簿式信息组织（半透明面板质感）
- 蓝色主操作、青色证据、独立状态色；明暗双主题同等层级
- 定制“小蜗”字标，不使用官方校徽或卡通吉祥物

## Colors

色彩以冷白、墨灰和克制的科大蓝为骨架，青色只承担检索与证据语义，状态色各自独立。

### Primary

- **科大操作蓝** (`primary`): 只用于当前工作区、当前视图和主操作；悬停使用 `primary-hover`，选中背景使用 `primary-soft`。

### Secondary

- **证据青** (`retrieval`): 只用于联网检索、来源核验和证据可信状态；浅色背景使用 `retrieval-soft`。

### Tertiary

- **确认绿、演示琥珀、风险红** (`success`, `warning`, `danger`): 分别表达批准或成功、演示或待确认、失败或破坏性操作；对应 soft 色只作状态底色。

### Neutral

- **冷纸画布** (`canvas`): 应用底层背景。
- **连续工作面** (`surface`, `surface-raised`, `surface-muted`): 正文、浮层和次级数据带。
- **墨色正文** (`ink`, `muted`, `faint`): 主文、辅助文和低优先级元数据。
- **编目规则线** (`line`, `line-strong`): 分区、表格和组件边界。
- **索引脊** (`rail`, `rail-raised`, `rail-ink`): 桌面导航基座；浅色主题为白玻璃（`--glass-weak` + blur），深色主题为暗玻璃。
- **夜间工作面** (`dark-canvas`, `dark-surface`, `dark-surface-raised`, `dark-ink`, `dark-muted`, `dark-line`): 深色主题的对应层级；操作蓝和证据青分别切换为 `dark-primary` 与 `dark-retrieval`。

**The One Meaning Per Accent Rule.** 蓝色只表示当前状态或主操作，青色只表示检索与证据，演示数据始终使用 warning 语义；不要互换。

## Typography

**Display Font:** Noto Sans SC Variable（Microsoft YaHei UI 回退）
**Body Font:** Noto Sans SC Variable（Microsoft YaHei UI 回退）
**Label/Mono Font:** Cascadia Code（Consolas 回退，仅用于代码、URL 和 generation 标识）

**Character:** 中文无衬线字体提供高密度界面的稳定阅读节奏；字重而非夸张字号建立层级。系统不使用负字距，紧凑来自结构而不是压缩文字。

### Hierarchy

- **Headline**（760，22px，1.25）：工作区标题。
- **Title**（730，16px，1.4）：内容分区、目录组和关键任务标题。
- **Body**（400，14px，1.7）：正文、描述和数据内容；移动端正文与输入提升至 16px。
- **Label**（650，12px，1.4）：状态、时间、列名和辅助信息，不得低于 12px。
- **Mono**（400，12px，1.6）：URL、课程代码、版本与 generation 标识。

**The Legibility Floor Rule.** 桌面正文至少 14px、辅助信息至少 12px；移动正文和输入至少 16px。任何元数据都不能以 8–11px 换取密度。

## Layout

桌面端在 1200px 及以上使用 216px 深色索引脊；761–1199px 收为 72px 图标轨，名称通过 tooltip 与工作区标题补足。760px 及以下切换为顶部字标、底部工作区导航和单列正文。聊天历史在平板与移动端进入抽屉，审核队列在这些宽度采用“索引页到全宽详情页”的单面板流，不与正文并排挤压。

主要内容宽度控制在约 1160px 内，以 14–34px 的响应式页边距和 1px 规则线维持编目节奏。桌面控件高度至少 40px，移动触控目标至少 44px；320px 起不得产生整页横向溢出。表格和课表可在自身容器内横向滚动，但不能扩张页面。

## Elevation & Depth

系统以“玻璃关键层 + 平面内容层”分层：离工作面的元素（桌面索引脊、输入区、账号菜单、确认对话框、移动顶栏与底栏）使用半透明玻璃材质（`--glass` 系列 + `backdrop-filter: var(--gblur)` + `--gshadow`），静态页面内容依靠半透明面板（`--glass-weak`，无 blur）、明度层级和 1px 玻璃边框分层，普通目录行和数据带不使用漂浮阴影。

### Glass Budget（性能硬预算，为 Mate 70 Pro+ 等移动设备保帧）

- 手机常驻 backdrop-filter 层 ≤ 2（顶栏 + 底部导航；composer 用半透明实底）
- 桌面常驻 backdrop-filter 层 ≤ 3（索引脊 + 输入区 + 临时浮层）
- 消息列表内卡片 0 层 blur：用 `--glass-weak` 半透明填充 + 1px `--gborder-soft` 边框
- blur 半径上限：浅 14px / 深 16px；禁止 transition backdrop-filter 与 box-shadow
- 彩斑背景零 filter：纯 CSS radial-gradient 双伪层，主题切换只过渡 opacity
- 不支持 backdrop-filter 或用户偏好低透明度时，全部玻璃表面回退 `--glass-solid` 实底
- ⚠ 祖先链禁改：`.app-shell`/`.workspace-transition` 到 body 之间不得加 filter/transform/opacity<1/contain:paint，否则切断 backdrop 采样

### Shadow Vocabulary

- **玻璃阴影** (`--gshadow` / `--gshadow-lg`): 玻璃表面专用，含 inset 高光；主题切换不参与过渡。
- **低层浮起** (`0 7px 22px rgb(31 48 59 / 0.08)`): 非玻璃轻量反馈和悬浮操作。
- **覆盖层** (`0 16px 42px rgb(31 48 59 / 0.12)`): 非玻璃菜单和对话框。

**The Flat By Default Rule.** 页面分区和重复条目保持平面；只有覆盖当前任务面的交互层可以使用阴影。

## Shapes

形状语言紧凑、机械而不过度严厉：状态标签使用 3px，控件使用 5px，浮层、启动方块和真正有边界的工具使用 6px。头像可以是圆形；普通按钮、标签和数据容器不使用胶囊形。数据带与账簿保持方正连续；启动方块只用于明确的高频入口或常见任务，不承载页面分区。

彩色 2–3px 侧条不属于本系统。选中与审核状态通过图标、文字、背景和最多 1px 的结构边界表达。

## Components

### Buttons

- **Shape:** 紧凑矩形（5px 圆角），桌面高度至少 40px，移动高度至少 44px。
- **Primary:** 科大操作蓝底、白字，只用于当前任务的主要命令。
- **Hover / Focus:** 悬停加深背景；键盘焦点使用 2px 证据青外轮廓并保持 2px 间距。
- **Secondary / Danger:** 次级按钮使用抬升工作面与规则线；危险操作使用风险红并在不可逆操作前进入确认对话框。

### Chips

- **Style:** 3px 圆角、12px 标签字。演示、成功、风险和证据分别使用自己的语义色与浅底色。
- **State:** 文本和图标必须同时表达语义，不允许只有颜色差异。

### Cards / Containers

- **Corner Style:** 只有浮层、独立工具和真实边界容器使用 6px；页面分区不做浮卡。
- **Background:** 工作面和次级工作面按层级使用 surface 系列。
- **Shadow Strategy:** 静态容器无阴影，遵循 Flat By Default Rule。
- **Border:** 1px 规则线；重复数据优先使用连续行或数据带。
- **Internal Padding:** 常用 14–20px，紧凑目录行按 13px 垂直节奏。
- **Launch Tiles:** 使用 6px 以内圆角、1px 结构线和稳定宽高，不使用浮起、渐变或重阴影；页面内最多 8 个精选启动方块，不嵌套卡片。

### Inputs / Fields

- **Style:** 抬升工作面、1px 强规则线、5px 圆角；输入正文桌面 14px、移动 16px。
- **Focus:** 边界切换到操作蓝并增加克制的同色焦点环。
- **Error / Disabled:** 错误使用风险色文字与浅底；禁用态降低不透明度但保留可读标签。

### Navigation

用户端桌面索引脊使用图标加文字，当前工作区以操作蓝语义背景和文字表达，不使用彩色侧条。平板只保留 72px 图标轨并提供 tooltip；移动端使用固定底部导航，当前项以操作蓝浅底明确显示。管理后台不进入用户端导航，使用独立 `/admin` 壳、深色侧栏和管理青绿强调色，并持续显示环境与命名空间。

### Launch Tiles, Catalogs and Ledgers

校园服务先展示 8 个由 `config/links.yaml` 的 `featured/priority` 驱动的高频启动方块，再将其余入口按真实类别组成 4/3/2 列目录；搜索时隐藏精选区并只显示去重结果。社区“校园工具”作为第三个主视图，使用独立的 4/3/2 列方块目录，始终显示名称、可选说明、域名、分类和“管理员审核”，不得冒充官方配置。用户暂不选择图标。聊天空会话按能力显示 6 个常见任务方块，点击只填入并聚焦输入框。培养方案模块继续使用连续账簿行展示模块、学分和课程数量。禁止把官方入口和社区工具混成同一信任层级。

### Timetable

课表使用连续时间轴和周一至周日七列，课程块按结构化起止时间定位；官方 1–13 小节、第一至第四常用大课段及晚间时段以文字对应。前周、本周、后周和当前时间线必须可用；重叠课程并排并显示警示。卡片点击打开详情，详情只保留一个“问问小蜗”。无法确认星期、周次或时间的数据进入“待确认”，不画入时间轴。课程颜色与课程类型图标本轮不作语义映射。

### Evidence and Review States

联网阶段、来源和可信标记使用证据青。管理后台默认打开工具申请队列，支持搜索、状态筛选、批准、强制原因驳回、下架和审计；移动端使用队列到详情的单面板流并提供明确返回。知识审核分块继续使用待定、批准、排除三态 radio，状态变化作用于整块浅背景和文本；只对这一类状态所属变化使用短过渡，并完整遵守 reduced-motion。

## Do's and Don'ts

### Do:

- **Do** 让身份、来源、时效、演示隔离和证据门槛持续可见。
- **Do** 用精选启动方块、分类目录、连续数据带、账簿和 1px 规则线组织信息。
- **Do** 在 761–1199px 使用 72px 图标轨，在 760px 以下使用单列任务流。
- **Do** 为深浅主题、键盘焦点、320px 布局和 reduced-motion 提供同等完整度。

### Don't:

- **Don't** 把应用改成营销首页、Hero、无分组同权卡片墙或通用后台模板。
- **Don't** 混用操作蓝、证据青、演示 warning 和状态色的语义。
- **Don't** 使用 8–11px 可见文字、低于目标的触控区域或彩色粗侧条。
- **Don't** 超出玻璃预算新增 backdrop-filter 表面，或对玻璃表面过渡 backdrop-filter/box-shadow。
- **Don't** 在页面分区里嵌套卡片、使用大圆角胶囊、装饰渐变或泛化入场淡入。
- **Don't** 使用官方校徽、虚构校园素材或未核验的真实性暗示。
