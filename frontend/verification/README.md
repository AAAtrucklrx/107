# 响应式浏览器验收

脚本直接使用项目的 Playwright 依赖和本机 Chromium，不启动或访问真实后台。所有 `/api` 请求均由 `fixtures.mjs` 返回合成身份、课表、成绩、培养方案及 SSE；所有其他来源的网络请求被阻断。未定义的 API 会记录为失败，不转发到 Vite 的后台代理。

从 `frontend/` 运行：

```bash
PLAYWRIGHT_BROWSERS_PATH=/home/aaatrucklrx/.cache/xiaowo-playwright \
  node verification/responsive-check.mjs --phase=before --url=http://127.0.0.1:5174

PLAYWRIGHT_BROWSERS_PATH=/home/aaatrucklrx/.cache/xiaowo-playwright \
  node verification/responsive-check.mjs --phase=after --url=http://127.0.0.1:5175 --strict=true
```

`before` 使用 `.superpowers/frontend-rebuild-baseline` 的只读 Vite 快照，输出首页、学业总览、课表 × 375 / 768 / 1440 三档浅色截图。`after` 使用当前源码，额外覆盖深色、培养方案、我的、长表格聊天和键盘流程。页面时间固定为 2026-10-26 10:00（上海），数据中“合成”标识保留。

输出位于 `docs/frontend-rebuild/`。`before-verification.json` / `after-verification.json` 包含每页截图、视口/文档宽度、可见触控目标尺寸与不足 44px 的目标明细、浏览器错误和未定义请求。内文链接单独列为 `inlineProseLinks`，不计为独立控件的 44px 失败。

自动检查：

- 页面无横向溢出；课表在手机内部横滑，平板/桌面完整展开。
- 可见按钮、标签页、输入控件、课表课程与可点击表格行至少 44 × 44px。
- 历史抽屉可用 Enter 打开，Tab 留在抽屉，Escape 关闭并返回触发按钮。
- SSE 长表格局部滚动，不撑开整页。
- 手机视口缩小后输入框仍可见。此项仅为浏览器视口检查，不能替代 Android 真机输入法验收。
- WCAG AA 相对亮度：11 组主 tokens 前景/背景配对，以及实际可见文本。普通文本至少 4.5:1，大文本至少 3:1；`on-primary / primary` 同时覆盖深色模式。

对比度通过浏览器计算颜色并对祖先的纯色半透明背景作合成。渐变、背景图片和低透明度文字记录为跳过；不认证栅格图案、鼠标悬停状态、纯图标对比度、抗锯齿或遮挡情况。内文链接适用行内文本例外，因此不计入独立控件的触控失败。不要把此脚本的结果称为完整 WCAG 合规认证。

`--strict=true` 在溢出、小触控目标、已测对比度、交互失败或未定义 API 时退出码为 1；浏览器异常始终返回非零。基线允许记录原有缺陷用于前后对比。

## 2026-10-26 实测

- 基线：9 张截图，0 页面横溢出，40 次可见触控目标小于 44px；手机课表强制缩七列，横滑检查失败。0 浏览器异常、0 未定义 API、0 外部请求。
- 重构后：严格模式返回 0。38 张浅/深色截图，页面横溢出、触控尺寸、已测对比度、交互、浏览器异常和未定义 API 均为 0 项失败；没有外部请求。
- 手机课表：375px 视口下容器 341px、网格 840px，仅局部横滑；768px 下 646/646px，1440px 下 1062/1062px。
- 历史抽屉 Enter/Tab/Escape/返回焦点通过。375px 视口缩至 480px 高后，输入框底边 294px，仍在可见范围。
- 最终移除了品牌和联网按钮的旧渐变：1,698 次实际文本检查、418 次 tokens 配对、518 次可见控件尺寸检查均通过，没有跳过项；深色主按钮 tokens 配对实测 8.17:1。检测范围仍限于上方说明，不能替代完整无障碍审计。
- 已人工查看基线手机课表、改后手机课表、平板成绩、桌面深色首页、手机深色培养方案截图。

额外实际浏览器交互：

```bash
PLAYWRIGHT_BROWSERS_PATH=/home/aaatrucklrx/.cache/xiaowo-playwright node verification/interaction-check.mjs
```

`interaction-verification.json` 记录七项跨三档宽度检查：奶蛙点击→笑→idle、60秒sleep与busy优先、减少动画、桌面真实拖拽回弹不误点击、六行输入和奶蛙/Tab不重叠、普通Markdown宽表局部滚动、长回答“回到最新”可点击。该脚本使用合成API和浏览器时钟；没有真实学校认证操作。

## 小蛙助手双风格验收

`responsive-check.mjs` 新增 `--style=frog` / `--style=ustc`，分别配合 `--output=docs/xiaowa-styles/frog` 和 `--output=docs/xiaowa-styles/ustc` 保存证据。两种风格各覆盖浅深色与三端宽度。`style-switch-check.mjs` 验证真实控件切换后草稿、答复、身份与离线状态不丢失，刷新保持偏好。
