# 贡献指南（Contributing）

感谢你愿意改进 **astrbot_plugin_bili_all_function**！本文说明本地开发环境、
代码规范与提交流程。提交 PR 即表示你同意以 [AGPL-3.0](./LICENSE) 授权你的贡献。

## 快速开始

```bash
git clone https://github.com/existyay/astrbot_plugin_bili_all_function.git
cd astrbot_plugin_bili_all_function

python -m venv .venv
. .venv/Scripts/activate            # Windows（Linux/macOS：source .venv/bin/activate）
pip install -r requirements.txt -r requirements-dev.txt
pre-commit install                  # 提交前自动执行同一套检查
```

验证环境：

```bash
pytest                 # 全量测试 + 覆盖率门禁（≥95%）
ruff check .           # 静态检查
mypy                   # 类型检查
python scripts/check_metadata.py      # 元数据 / 配置 schema 自检
python scripts/check_release.py       # 发布前一键门禁（上面几项的组合）
```

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `main.py` | 插件主体：配置、HTTP 层、轮询、指令与事件 handler |
| `bili_constants.py` | 常量、接口地址、配置默认值与上限 |
| `bili_helpers.py` | 纯函数：解析、排版、脱敏、校验（无副作用，最易测试） |
| `bili_logic.py` | 消息链构建与动态过滤规则 |
| `bili_models.py` | `Subscription` 数据模型 |
| `_conf_schema.json` | 插件配置 schema（面板可视化编辑） |
| `metadata.yaml` | AstrBot 插件元数据（市场 / 安装校验用） |
| `views/` | 插件页面（Dashboard 内嵌视图） |
| `.astrbot-plugin/i18n/` | 插件页面与元数据的多语言文案 |
| `tests/` | 测试套件；`tests/fixtures/` 为线上接口真实快照（离线运行） |
| `scripts/` | 开发与发布辅助脚本 |

## 代码规范

- **Python 3.10+**，`from __future__ import annotations`，类型注解尽量完整；
- 代码风格由 `ruff format` 决定（行宽 120），提交前请执行
  `ruff format .`（`pre-commit` 也会自动执行）；
- 静态检查规则见 `pyproject.toml` 的 `[tool.ruff.lint]`：`E,F,W,I,UP,B,C4,SIM,RUF,PIE,RET,BLE,ASYNC`；
  中文全角标点（`RUF001/002/003`）已显式放行；
- `mypy` 必须零错误（当前对 5 个源码模块开启 `disallow_untyped_defs`）；
- 注释与文档字符串使用中文，写明**为什么**这么做（尤其是接口兼容与反风控的处理）；
- 新增对外行为（指令、配置项）请同步更新 `README.md` 与 `CHANGELOG.md`。

## 测试要求

- 新增/修改逻辑必须有测试，整体覆盖率 **不低于 95%**（当前约 99.9%）；
- 网络请求一律通过 `tests/conftest.py` 的 `FakeSession` / `route` 构造，
  **测试不得联网**；需要新快照时用 `python scripts/refresh_fixtures.py` 抓取并裁剪后提交；
- 与 AstrBot 安装/市场相关的约束写在 `tests/test_compliance.py`
  （元数据、配置 schema、i18n、插件页面、发布资产），修改这些资产时请一并更新；
- 破坏了兼容性（如 `metadata.yaml` 的 `astrbot_version`、`_conf_schema.json` 字段名）请在 PR 中说明。

## 提交与 PR

1. 从 `master` 切出分支：`git checkout -b fix/xxx`；
2. 提交信息使用 `fix:` / `feat:` / `docs:` / `chore:` 前缀，正文说明动机与影响；
3. 推送前执行 `python scripts/check_release.py`，确保与 CI 一致；
4. PR 描述中请包含：变更点、验证方式（贴出命令与结果）、是否影响已有配置或订阅数据。

CI 会执行：`ruff check`、`ruff format --check`、`mypy`、Python 3.10/3.12 双版本 `pytest`
（含覆盖率与徽章一致性校验）、AstrBot 合规自检与元数据校验。

## 报告问题

- 功能异常 / 使用疑问 → 使用 Issue 模板，附上 `/bili_state` 输出（**请先脱敏 Cookie**）；
- 安全问题 → 请按 [SECURITY.md](./SECURITY.md) 私下报告，不要公开提交。

## 行为准则

参与本项目即表示你同意保持友善、专业、就事论事的沟通方式；
不接受人身攻击、歧视性言论或任何形式的骚扰。
