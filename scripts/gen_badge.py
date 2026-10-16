"""生成覆盖率徽章（``docs/badges/coverage.svg``）。

用法::

    python scripts/gen_badge.py            # 使用已有 .coverage 数据生成徽章
    python scripts/gen_badge.py --test     # 先跑一遍测试再生成徽章
    python scripts/gen_badge.py --check    # 仅校验已提交的徽章是否最新（CI / pre-commit）

徽章为 shields.io 风格的静态 SVG，颜色阈值与 ``coverage-badge`` 保持一致
（>=95% 亮绿 / >=90% 绿 / >=75% 黄绿 / >=60% 黄 / >=40% 橙 / 其余红），
且不依赖 ``pkg_resources`` 等已废弃依赖。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BADGE_PATH = ROOT / "docs" / "badges" / "coverage.svg"

COLORS = {
    "brightgreen": "#4c1",
    "green": "#97CA00",
    "yellowgreen": "#a4a61d",
    "yellow": "#dfb317",
    "orange": "#fe7d37",
    "red": "#e05d44",
}
COLOR_RANGES = [
    (95, "brightgreen"),
    (90, "green"),
    (75, "yellowgreen"),
    (60, "yellow"),
    (40, "orange"),
    (0, "red"),
]
CHANNEL = "#555"
FONT = "Verdana,Geneva,DejaVu Sans,sans-serif"


def pick_color(percent: float) -> str:
    for threshold, name in COLOR_RANGES:
        if percent >= threshold:
            return COLORS[name]
    return COLORS["red"]


def text_width(text: str) -> int:
    """粗略估算 Verdana 11px 下的文本宽度（与 shields.io 观感接近）。"""
    return sum(11 if ch.isupper() or ch.isdigit() else 7 for ch in text)


def render_badge(label: str, value: str, color: str) -> str:
    label_w = text_width(label) + 10
    value_w = text_width(value) + 10
    total_w = label_w + value_w
    label_cx = int(label_w * 10 / 2)
    value_cx = int((label_w + value_w / 2) * 10)

    def texts(x: int, text: str) -> str:
        length = int(text_width(text) * 10)
        return (
            f'<text x="{x}" y="150" fill="#010101" fill-opacity=".3" '
            f'transform="scale(.1)" textLength="{length}">{text}</text>'
            f'<text x="{x}" y="140" transform="scale(.1)" '
            f'textLength="{length}">{text}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="20" role="img" aria-label="{label}: {value}">
<title>{label}: {value}</title>
<linearGradient id="s" x2="0" y2="100%">
<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
<stop offset="1" stop-opacity=".1"/>
</linearGradient>
<clipPath id="r"><rect width="{total_w}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)">
<rect width="{label_w}" height="20" fill="{CHANNEL}"/>
<rect x="{label_w}" width="{value_w}" height="20" fill="{color}"/>
<rect width="{total_w}" height="20" fill="url(#s)"/>
</g>
<g fill="#fff" text-anchor="middle" font-family="{FONT}" font-size="110">
{texts(label_cx, label)}
{texts(value_cx, value)}
</g>
</svg>
"""


def current_percent() -> float:
    import coverage

    cov = coverage.Coverage()
    cov.load()
    with open(os.devnull, "w") as devnull:
        return float(cov.report(file=devnull))


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成覆盖率徽章")
    parser.add_argument("--test", action="store_true", help="先生成覆盖率数据（运行 pytest）")
    parser.add_argument("--check", action="store_true", help="仅校验徽章是否最新，不写入文件")
    parser.add_argument("--output", default=str(BADGE_PATH), help="徽章输出路径")
    parser.add_argument("--label", default="coverage", help="徽章左侧文字")
    args = parser.parse_args()

    if args.test:
        code = run([sys.executable, "-m", "pytest", "-q", "--no-cov"])
        if code != 0:
            print("测试失败，已停止生成徽章", file=sys.stderr)
            return code

    percent = current_percent()
    # 向下取整，避免 99.9% 被显示成 100%
    value = f"{int(percent)}%" if percent < 100 else "100%"
    output = Path(args.output)
    rendered = render_badge(args.label, value, pick_color(percent))

    if args.check:
        if not output.is_file():
            print(f"徽章文件不存在：{output}", file=sys.stderr)
            return 1
        if output.read_text(encoding="utf-8") != rendered:
            print(
                f"徽章已过期（当前覆盖率 {percent:.2f}%），请执行 python scripts/gen_badge.py 后提交",
                file=sys.stderr,
            )
            return 1
        print(f"徽章已是最新（{value}）")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"覆盖率 {percent:.2f}% → 徽章 {output.relative_to(ROOT)}（{value}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
