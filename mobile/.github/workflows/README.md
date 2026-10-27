# CI位置

GitHub Actions只执行仓库根目录 `.github/workflows/` 下的工作流。
本项目实际工作流是 `../../../.github/workflows/mobile.yml`（相对于本文件所在目录）。
这里保留说明以对应移动端交付目录，避免放置一个不会执行的嵌套workflow。
