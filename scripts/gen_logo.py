"""生成插件图标 ``logo.png``（无外部素材依赖，可重复执行）。

AstrBot 面板 / 插件市场会读取插件根目录下的 ``logo.png`` 作为插件卡片图标，
因此该文件必须随插件一起提交，且体积要小（建议 < 100KB）。

用法::

    python scripts/gen_logo.py

设计：圆角方形渐变底（B站粉 #FB7299 → 品牌蓝 #23ADE5）+ 白色圆角字幕
「BILI」，四角留出安全边距，缩放到 64px 仍可辨识。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "logo.png"
SIZE = 512
RADIUS = 96

PINK = (251, 114, 153)
BLUE = (35, 173, 229)
WHITE = (255, 255, 255)


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def main() -> int:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # pragma: no cover - 仅开发环境需要
        print("需要 Pillow：pip install pillow", file=sys.stderr)
        return 1

    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 1) 圆角底 + 对角渐变
    gradient = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gdraw.line(
            [(0, y), (SIZE, y)],
            fill=(_lerp(PINK[0], BLUE[0], t), _lerp(PINK[1], BLUE[1], t), _lerp(PINK[2], BLUE[2], t), 255),
        )
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (SIZE - 1, SIZE - 1)], radius=RADIUS, fill=255)
    image.paste(gradient, (0, 0), mask)

    # 2) 白色圆角字幕
    box = (SIZE * 0.12, SIZE * 0.34, SIZE * 0.88, SIZE * 0.66)
    draw.rounded_rectangle(box, radius=48, fill=WHITE)

    # 3) 文本
    text = "BILI"
    font = None
    for name in ("arialbd.ttf", "arial.ttf", "seguisb.ttf", "msyhbd.ttc", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 150)
            break
        except OSError:
            continue
    if font is None:  # pragma: no cover - 字体缺失时退回默认位图字体
        font = ImageFont.load_default()

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tx = box[0] + ((box[2] - box[0]) - (right - left)) / 2 - left
    ty = box[1] + ((box[3] - box[1]) - (bottom - top)) / 2 - top
    draw.text((tx, ty), text, font=font, fill=(38, 38, 38, 255))

    image.save(TARGET, format="PNG", optimize=True)
    print(f"已生成 {TARGET.relative_to(ROOT)}（{TARGET.stat().st_size / 1024:.1f} KB，{SIZE}x{SIZE}）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
