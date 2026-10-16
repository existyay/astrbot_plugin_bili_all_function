"""发布前元数据自检。

直接复用测试套件中的校验用例（``tests/test_constants.py`` 的元数据/配置 schema 部分），
避免出现「文档说的校验」与「CI 跑的校验」两套实现。

用法::

    python scripts/check_metadata.py

校验内容：

- ``metadata.yaml`` 必填字段（``name`` / ``desc`` / ``version`` / ``author``）非空，
  可选字段类型合法，``astrbot_version`` 是合法的 PEP 440 约束；
- ``_conf_schema.json`` 为合法 JSON，且每一项的 ``type`` 都是 AstrBot 支持的编辑器类型；
- 插件版本号在 ``metadata.yaml`` 与 ``bili_constants.py`` 中保持一致；
- 插件不再使用已废弃的 ``@register`` 装饰器。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    "tests/test_constants.py",
    "-k",
    "metadata or schema or version or register or repo or assets",
]


def main() -> int:
    cmd = [sys.executable, "-m", "pytest", *TARGETS, "-q", "--no-cov", "-p", "no:cacheprovider"]
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
