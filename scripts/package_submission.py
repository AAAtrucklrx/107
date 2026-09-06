"""107 杯作品打包：源码（git archive）+ 提交材料 → 单个 zip。

安全设计：
- 源码取自 git HEAD（git archive），天然只含已提交内容——.env 密钥、data/、
  *.db、scripts/tmp_*、.venv 等未跟踪文件全部不会进入压缩包（铁律 4）。
- 打包前强制自检：确认 .env 不在 git 跟踪列表；未提交改动时警告。
- 压缩包命名遵循比赛要求：队长学号+队长手机号+智能体赛道+本科生队伍。

用法：
  py scripts/package_submission.py --leader-id PB00000000 --leader-phone 13800000000
  py scripts/package_submission.py --leader-id PB00000000 --leader-phone 13800000000 --skip-video
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "submission"
MATERIALS = [
    "作品简介_小蜗.docx",
    "作品简介_小蜗.pdf",
    "设计文档_小蜗.docx",
    "设计文档_小蜗.pdf",
    "演示视频分镜脚本.md",
]
DIAGRAMS = [  # 压缩包内扁平命名，避免文件夹形式
    ("diagrams/1-architecture.png", "图1-总体架构.png"),
    ("diagrams/2-qa-workflow.png", "图2-智能体工作流.png"),
    ("diagrams/3-evidence.png", "图3-联网证据流水线.png"),
    ("diagrams/4-streaming.png", "图4-流式响应时序.png"),
]
VIDEO_CANDIDATES = ["演示视频_小蜗.mp4", "demo.mp4", "演示视频.mp4"]


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败: {result.stderr.strip()}")
    return result.stdout


def precheck() -> None:
    tracked = _git("ls-files").splitlines()
    dangerous = [f for f in tracked if f == ".env" or f.startswith("scripts/data/steamtools")]
    if dangerous:
        raise SystemExit(f"危险文件被 git 跟踪，中止打包: {dangerous}")
    status = _git("status", "--porcelain", "--untracked-files=no")
    if status.strip():
        print("[警告] 工作区有未提交改动，打包的是 HEAD 版本（不含这些改动）:")
        print(status)


def build(leader_id: str, leader_phone: str, skip_video: bool) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    archive = OUT_DIR / "_source_head.zip"
    with open(archive, "wb") as fh:
        subprocess.run(
            ["git", "-C", str(ROOT), "archive", "--format=zip", "-o", str(archive), "HEAD"],
            check=True,
        )

    final_name = f"{leader_id}+{leader_phone}+智能体赛道+本科生队伍"
    final_path = OUT_DIR / f"{final_name}.zip"

    video: Path | None = None
    if not skip_video:
        for name in VIDEO_CANDIDATES:
            candidate = OUT_DIR / name
            if candidate.exists():
                video = candidate
                break
        if video is None:
            raise SystemExit(
                "未找到演示视频（submission/ 下应为 演示视频_小蜗.mp4）。"
                "录好后重跑，或用 --skip-video 先打不含视频的版本（注意 4 项材料缺一不可）。"
            )

    with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(archive, "小蜗-源码.zip")
        archive.unlink()
        for name in MATERIALS:
            material = OUT_DIR / name
            if not material.exists():
                raise SystemExit(f"缺少提交材料: {material}")
            zf.write(material, name)
        for src, arcname in DIAGRAMS:
            diagram = OUT_DIR / src
            if not diagram.exists():
                raise SystemExit(f"缺少图示文件: {diagram}")
            zf.write(diagram, arcname)
        if video is not None:
            zf.write(video, "演示视频_小蜗.mp4")
        # 部署说明：在线体验入口前置，离线部署步骤紧随其后
        zf.writestr(
            "部署说明.txt",
            "【在线体验（推荐先看）】\n"
            "打开 http://114.214.241.119:8850 即可直接体验全部功能\n"
            "（演示身份已预置个人课表/成绩等演示数据；全部个人数据均为合成演示数据）。\n"
            "源码仓库（公开可读，与本包源码一致）：https://github.com/AAAtrucklrx/107\n"
            "快速体验路径：登录演示身份 → 问\"我的成绩怎么样\" → 问\"推荐几门通识课\" → "
            "切\"我的学业\"看课表 → 时效类问题（如\"最新的选课通知\"）体验联网证据与来源核验。\n\n"
            "【离线部署】\n"
            "环境要求：Python 3.12+、Node.js 18+；第 3 步的科大 LLM 平台 API Key 可在\n"
            "https://llm.ustc.edu.cn 申请（科大师生均可，申请后约 1-2 天生效）。\n"
            "1. pip install -r requirements.txt\n"
            "2. cd frontend && npm ci && npm run build && cd ..\n"
            "3. 复制 .env.example 为 .env 并填入科大 LLM 平台密钥（api.llm.ustc.edu.cn）\n"
            "4. python init_check.py\n"
            "5. python -m uvicorn xiaowo_web.main:app --host 127.0.0.1 --port 8000\n"
            "注：不配置联网搜索 Key 时问答/学业功能仍可用，仅联网证据通道降级为如实提示。\n"
            "详细文档见源码包 README.md 与 docs/Web部署与数据迁移.md；"
            "联网抓取依赖 Crawl4AI sidecar（deploy/sidecars/compose.yml）。\n",
        )

    # 终检：压缩包内不得出现密钥文件
    with zipfile.ZipFile(final_path) as zf:
        names = zf.namelist()
        leaks = [n for n in names if n.endswith("/.env") or n == ".env"]
        if leaks:
            final_path.unlink()
            raise SystemExit(f"压缩包内发现 .env，已销毁重打: {leaks}")
        print(f"包内文件 {len(names)} 项:")
        for n in names:
            print(f"  - {n}")
    print(f"\n完成: {final_path} ({final_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print("提交：队长登录 v.ustc.edu.cn → 我的资源 → 文件 → 上传该 zip → 分享给个人账号 P0581（勾选允许复制+允许下载）")
    return final_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leader-id", required=True, help="队长学号")
    parser.add_argument("--leader-phone", required=True, help="队长手机号")
    parser.add_argument("--skip-video", action="store_true", help="暂不打视频（材料不齐，慎用）")
    args = parser.parse_args()
    precheck()
    build(args.leader_id, args.leader_phone, args.skip_video)


if __name__ == "__main__":
    sys.exit(main())
