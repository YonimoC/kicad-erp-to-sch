"""生成插件图标（Pillow）。

用法: python generate_icons.py
输出:
    plugins/icons/erp_to_sch.png        24x24  浅色主题
    plugins/icons/erp_to_sch_dark.png   24x24  深色主题
    resources/icon.png                  64x64  PCM 图标
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
SCALE = 8


def draw_icon(size: int, dark: bool) -> Image.Image:
    s = size * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 背景圆角方块
    bg = (28, 61, 92, 255) if dark else (32, 92, 140, 255)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=bg)

    # 电阻折线（横向）
    cx, cy = s * 0.5, s * 0.42
    amp = s * 0.10
    step = s * 0.07
    pts = [(s * 0.16, cy)]
    x = s * 0.22
    sign = 1
    for _ in range(6):
        pts.append((x, cy - amp * sign))
        x += step
        sign = -sign
    pts.append((x, cy))
    pts.append((s * 0.84, cy))
    d.line(pts, fill=(255, 255, 255, 255), width=int(s * 0.055), joint="curve")

    # 两端引脚
    d.line((s * 0.10, cy, s * 0.16, cy), fill=(255, 255, 255, 255), width=int(s * 0.055))
    d.line((s * 0.84, cy, s * 0.90, cy), fill=(255, 255, 255, 255), width=int(s * 0.055))

    # 下方 "ERP" 三条短线，表示数据表
    bar_y = s * 0.70
    for i, w in enumerate((0.44, 0.34, 0.26)):
        y = bar_y + i * s * 0.09
        d.rounded_rectangle(
            (s * (0.5 - w / 2), y, s * (0.5 + w / 2), y + s * 0.05),
            radius=int(s * 0.025),
            fill=(255, 200, 90, 255) if i == 0 else (255, 255, 255, 190),
        )

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    icons = ROOT / "plugins" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    (ROOT / "resources").mkdir(parents=True, exist_ok=True)

    draw_icon(24, dark=False).save(icons / "erp_to_sch.png")
    draw_icon(24, dark=True).save(icons / "erp_to_sch_dark.png")
    draw_icon(64, dark=False).save(ROOT / "resources" / "icon.png")
    print("图标已生成:")
    for p in (icons / "erp_to_sch.png", icons / "erp_to_sch_dark.png", ROOT / "resources" / "icon.png"):
        print(f"  {p}")


if __name__ == "__main__":
    main()
