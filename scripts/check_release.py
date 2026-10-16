"""发布前一键门禁：本地执行与 CI 完全一致的全部检查。

用法::

    python scripts/check_release.py            # 全量检查
    python scripts/check_release.py --fast     # 跳过测试（只跑静态检查与元数据）

依次执行：

1. ``ruff format --check .``        —— 代码风格与 CI 一致；
2. ``ruff check .``                 —— 静态检查（生产级规则集）；
3. ``mypy``                         —— 类型检查（5 个源码模块，零容忍）；
4. ``pytest tests/test_compliance.py`` —— AstrBot 安装/市场合规自检；
5. ``python scripts/check_metadata.py`` —— 元数据与配置 schema 自检；
6. ``pytest``                       —— 全量测试 + 覆盖率门禁（≥95%），
   并校验已提交的覆盖率徽章是否为最新。

任一步失败即以该步的退出码结束，方便直接接入 CI 或 pre-push 钩子。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(title: str, cmd: list[str]) -> int:
    print(f"\n=== {title} ===")
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="发布前门禁检查")
    parser.add_argument("--fast", action="store_true", help="跳过全量测试，只跑静态检查与合规自检")
    args = parser.parse_args()

    py = sys.executable
    steps: list[tuple[str, list[str]]] = [
        ("格式化检查 (ruff format)", [py, "-m", "ruff", "format", "--check", "."]),
        ("静态检查 (ruff check)", [py, "-m", "ruff", "check", "."]),
        ("类型检查 (mypy)", [py, "-m", "mypy"]),
        (
            "AstrBot 合规自检 (compliance)",
            [py, "-m", "pytest", "tests/test_compliance.py", "-q", "--no-cov", "-p", "no:cacheprovider"],
        ),
        ("元数据校验 (metadata)", [py, "scripts/check_metadata.py"]),
    ]
    node = shutil.which("node")
    if node:
        steps.append(("插件页面渲染冒烟 (node)", [node, "scripts/check_view.mjs"]))
    else:
        print("\n跳过插件页面渲染冒烟：未检测到 node")
    if not args.fast:
        steps.append(
            ("全量测试 + 覆盖率门禁 (pytest)", [py, "-m", "pytest", "-p", "no:cacheprovider"]),
        )
        steps.append(("覆盖率徽章校验", [py, "scripts/gen_badge.py", "--check"]))

    for title, cmd in steps:
        code = run(title, cmd)
        if code != 0:
            print(f"\n[未通过] {title}", file=sys.stderr)
            return code

    print("\n[通过] 全部检查通过，可以发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
