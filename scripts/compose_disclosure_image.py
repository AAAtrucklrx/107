"""开发插曲素材：两张手机截图（校方邮件回复）裁掉状态栏/底栏后并排拼图。

输出 submission/diagrams/6-disclosure-emails.png（白底、等高、带 12px 间隔与 1px 灰边）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

SRC = [
    Path(r"C:\Users\Richelieu\AppData\Roaming\QoderCN\SharedClientCache\cache\images\task-3c0\Image_1788661635387_492-c67704aa.jpg"),
    Path(r"C:\Users\Richelieu\AppData\Roaming\QoderCN\SharedClientCache\cache\images\task-3c0\Image_1788661637912_211-c5fe00d7.jpg"),
]
OUT = Path(__file__).resolve().parent.parent / "submission" / "diagrams" / "6-disclosure-emails.png"

CROP_TOP = 150   # 状态栏+应用头
CROP_BOTTOM = 170  # 底部导航
GAP = 36
BORDER = (208, 214, 220)


def main() -> None:
    images = []
    for path in SRC:
        im = Image.open(path).convert("RGB")
        im = im.crop((0, CROP_TOP, im.width, im.height - CROP_BOTTOM))
        im = ImageOps.expand(im, border=1, fill=BORDER)
        images.append(im)

    target_h = min(im.height for im in images)
    resized = []
    for im in images:
        w = round(im.width * target_h / im.height)
        resized.append(im.resize((w, target_h), Image.LANCZOS))

    total_w = sum(im.width for im in resized) + GAP * (len(resized) + 1)
    canvas = Image.new("RGB", (total_w, target_h + GAP * 2), "white")
    x = GAP
    for im in resized:
        canvas.paste(im, (x, GAP))
        x += im.width + GAP
    canvas.save(OUT)
    print(f"拼图完成: {OUT} ({canvas.width}x{canvas.height})")


if __name__ == "__main__":
    main()
