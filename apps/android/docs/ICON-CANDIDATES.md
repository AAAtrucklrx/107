# 图标候选

候选板：`assets/branding/icon-candidates/contact-sheet.png`。

| 编号 | 名称 | 方向 |
| --- | --- | --- |
| 01 | 轨道螺旋 | 深蓝科技感，最接近“学术轨道”概念 |
| 02 | 学术印记 | 米白编辑风，正式且易识别 |
| 03 | 知识节点 | 强调知识图谱与连接 |
| 04 | 一笔路径 | 极简、最接近主项目原始字标 |
| 05 | 档案网格 | 数据库与资料归档语义 |
| 06 | 折页知识 | 文档、论文与知识库语义 |
| 07 | 深空轨道 | 更强科技与夜间辨识度 |
| 08 | 柔和蜗壳 | 亲和但不使用卡通角色 |
| 09 | 负形书页 | 书本与螺旋结合，适合学术产品 |
| 10 | 校园等高线 | 校园地图和探索语义 |

每套都提供 1024×1024 SVG 与 PNG，无文字、无校徽，并保留 Android 圆形/圆角方形裁切安全区。

## 当前定稿

2026-09-04 采用候选板第 04 个“一笔路径”。正式图标直接使用现有蓝色一笔蜗壳、青色触角和白色背景，不再使用 ImageGen 定稿。

源文件：

- `assets/branding/icon-candidates/04-continuous-path.svg`：完整候选图
- `assets/branding/icon-candidates/04-continuous-path-foreground.svg`：Android 自适应图标前景
- `assets/branding/icon-candidates/04-continuous-path-foreground.png`：Android 资源生成源

重新生成 Android 图标：

```powershell
npm run icon:selected
npm run branding
& .\scripts\build-debug.ps1
& .\scripts\build-release.ps1
```

候选生成脚本和定稿生成脚本都是确定性的，可重复产出相同 SVG/PNG。
