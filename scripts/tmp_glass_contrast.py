"""WCAG AA 对比度抽查：玻璃表面 + 彩斑最坏组合（阶段5 验收项）。"""
from __future__ import annotations


def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    lo, hi = sorted((lum(fg), lum(bg)))
    return (hi + 0.05) / (lo + 0.05)


def blend(top: tuple[float, float, float], alpha: float, bottom: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(alpha * t + (1 - alpha) * b for t, b in zip(top, bottom))


def hx(s: str) -> tuple[float, float, float]:
    s = s.lstrip("#")
    return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))


# 浅色：玻璃(白 .55) 叠最亮彩斑底(b2 青 ≈ #dcefe9 上界) 叠 canvas
light_bg_lightest = blend((255, 255, 255), 0.55, blend((220, 239, 233), 0.5, hx("eef2f8")))
# 深色：玻璃(rgb(24,33,46) .55) 叠最暗彩斑底(canvas 本身) 叠 canvas
dark_bg_darkest = blend((24, 33, 46), 0.55, hx("0a0f16"))
# 深色最坏情况其实是彩斑偏亮处：取 b1 蓝斑中心 rgb(18,58,110) 叠 canvas 再叠玻璃
dark_bg_blob = blend((24, 33, 46), 0.55, blend((18, 58, 110), 0.55, hx("0a0f16")))

cases = [
    ("light ink/main", hx("1c2430"), light_bg_lightest, 4.5),
    ("light muted/secondary", hx("566672"), light_bg_lightest, 4.5),
    ("light faint/meta(12px需4.5，已加深)", hx("5f6e7a"), light_bg_lightest, 4.5),
    ("light primary on glass", hx("034ea1"), light_bg_lightest, 4.5),
    ("dark ink/main", hx("e8eef6"), dark_bg_darkest, 4.5),
    ("dark muted/secondary", hx("a8b6bf"), dark_bg_blob, 4.5),
    ("dark faint/meta", hx("7f909b"), dark_bg_darkest, 4.5),
    ("dark primary on glass", hx("5b9be0"), dark_bg_darkest, 4.5),
    ("white on user-bubble(mid #1770ad≈)", hx("ffffff"), hx("1770ad"), 4.5),
]

fails = 0
for name, fg, bg, need in cases:
    r = ratio(fg, bg)
    ok = "PASS" if r >= need else "FAIL"
    if r < need:
        fails += 1
    print(f"{ok}  {name}: {r:.2f}:1 (need {need})")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILED'}")
