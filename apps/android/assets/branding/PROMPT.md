# 小蜗品牌图生成规格

源图文件：`xiaowo-mascot-v1.png`，PNG，至少 1024 x 1024，真实透明背景。

```text
Use case: logo-brand
Asset type: Android launcher mascot and splash artwork
Primary request: Create an original anime-inspired science-and-engineering snail mascot for the Xiaowo campus assistant.
Subject: A friendly compact snail with round glasses, a spiral shell shaped with subtle orbital geometry, and a few small cyan star points and coordinate marks.
Style/medium: polished 2D cel-shaded illustration, vector-friendly silhouette, crisp edges, expressive but not childish.
Composition/framing: centered square composition; complete subject fully visible; strong silhouette; generous transparent safe area; readable at 48 px; no thin isolated details near the edge.
Color palette: deep blue, cyan and cool white with restrained dark outlines.
Text (verbatim): ""
Constraints: genuinely transparent background; original character; no university emblem; no existing character likeness; no wordmark; no letters; no formulas rendered as readable text; no watermark.
Avoid: photorealism, 3D mockup, busy background, gradients outside the subject, excessive glow, tiny decorative clutter.
```

生成并检查源图后运行：

```powershell
npm run branding
```

脚本会生成 legacy、round、adaptive foreground 和 monochrome 图标，并记录源图与输出哈希。系统启动页直接使用 adaptive foreground。

2026-09-04 的第二批 `codex-image2` 请求因服务端 HTTP 403 未产出文件，因此仓库同时提供确定性的矢量候选：

```powershell
npm run icons:candidates
```

候选位于 `assets/branding/icon-candidates/`，选择说明见 `docs/ICON-CANDIDATES.md`。
