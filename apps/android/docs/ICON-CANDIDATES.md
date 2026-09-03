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

选择后执行：

```powershell
npm run branding -- --source assets/branding/icon-candidates/04-continuous-path.png
& .\scripts\build-debug.ps1
& .\scripts\build-release.ps1
```

当前 Demo APK 继续使用已生成的蓝白蜗牛形象，避免在选择前反复改包。候选生成脚本是确定性的，可重复产出相同 SVG/PNG。
