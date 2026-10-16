# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [v0.3.0] - 2026-10-16

### 修复

- **UP动态订阅无法获取最新动态**：
  - 新接口把置顶标记放在 `modules.module_author.is_top`，旧实现识别不到，
    导致订阅锚点被钉在置顶（较旧）动态上，最新动态永远被判为「已读」；
  - 推送批次从「未读动态中最旧的 N 条」改为「**最新的 N 条**」，并按时间正序发送；
  - 未读动态即使超出推送条数上限，也会全部写入去重缓存，避免下一轮重复轰炸。
- **动态/直播推送排版**：
  - 只有 `MODULE_TYPE_DESC`（无 `module_dynamic`）的动态正文不再丢失；
  - 统一为 `头部 → 正文 → 图片 → 链接` 的排版顺序，视频统计与转发原文摘要各占一行；
  - `/bili_sub_list`、`/bili_global_list` 改为单行紧凑格式（`UID ｜ 状态 ｜ 过滤`）；
  - 修复 `播放 2.5万` 等中文计数导致 `int()` 抛 `ValueError` 的崩溃。
- **动态接口偶发返回空列表**（`code=0` 且 `items=[]`，实测同一 UID 连续请求成功率仅 6~7 成）：
  现在会按 `empty_retry_count` 重试（默认 3 次、间隔递增），每次重试附加时间戳参数绕开
  空缓存，并把「读不到」与「没有新动态」区分开；读不到时**不更新订阅锚点**，
  下一轮会继续补推，避免漏推最新动态。
- 订阅时抓取一次直播状态作为初始基线，避免订阅瞬间误报「开播了」。

### 新增

- 适配 AstrBot 4.x 最新插件规范：移除已废弃的 `@register`，元数据统一由 `metadata.yaml` 提供，
  用户日志改用插件级 `self.logger`，数据目录通过 `StarTools.get_data_dir(插件名)` 显式定位。
- 新增 `features.send_video_info`（解析后是否回复视频信息）与 `features.enable_llm_tool` 开关；
  现在命中B站链接**真的会回复视频信息**（旧版本只解析不回复）。
- 新增 `subscription.uid_concurrency`、`empty_retry_count`（默认 3）、
  `empty_retry_delay_seconds`（默认 1.0s）等配置项。
- 新增 LLM 函数工具 `bili_video_info`。
- 管理员指令同时支持 AstrBot 原生管理员身份。
- 订阅数据改为**原子写入**（临时文件 + `os.replace`），进程中断不再损坏文件。
- 新增 `bili_constants.py` / `bili_helpers.py` / `bili_logic.py` / `bili_models.py` 模块化拆分。
- 新增完整测试套件（含线上真实接口快照夹具）、覆盖率门禁（≥95%）与
  `docs/badges/coverage.svg` 覆盖率徽章。
- 新增 `scripts/live_check.py`（联网联调）与 `scripts/refresh_fixtures.py`（刷新测试夹具）。

### 变更

- `/bili_state` 自检输出重排：配置检查、Cookie 检查、订阅检查、功能开关与轮询策略分区展示。
- 动态去重缓存 `recent_ids` 语义修正为 LRU（保留最新记录）。
- 轮询关闭单次请求重试，改为依赖下一轮轮询，避免轮询任务堆积。

### 安全与生产化加固

- **凭据保护**：带 Cookie 的请求新增 B站 域名白名单（`bilibili.com` / `b23.tv` / `bilivideo.com` 等），
  重定向到白名单外时自动剥离 Cookie；Cookie 中的 `\r` / `\n` / `\0` 会被清理以防头部注入；
  日志、`/bili_state` 与插件页面统一输出 `SESSDATA=abcd***` 形式的掩码。
- **输入护栏**：UID 必须为合法数字且小于 `10^12`；正则过滤器限制单条 200 字符、每个订阅 10 条；
  单条消息仅扫描前 4000 字符；LLM 工具入参限制 2000 字符；响应体上限 4 MB（超出不再重试）；
  下载文件名经安全清洗。
- **容量上限**：单会话订阅上限 50、总会话上限 500，超限时给出明确提示；
  `subscriptions.json` 新增 `__schema__` 版本号（当前 `2`），可在旧文件上平滑迁移并自动裁剪超限数据。
- **请求治理**：新增全局最小请求间隔 `subscription.min_request_interval_seconds`（默认 0.2s），
  重试等待改为「基础间隔 × 轮次 + 0~25% 抖动」，避免同步冲击上游。
- **可观测性**：新增运行时统计（请求/失败/静默 412/空响应/动态推送/直播推送/下载量/发送失败/轮询轮次），
  `/bili_state` 增加「运行统计」区块；新增 `health_snapshot()` 与只读插件 Web API
  `GET /api/v1/plugins/extensions/astrbot_plugin_bili_all_function/status`。
- **生命周期加固**：`initialize()` 幂等（重复调用不会重复注册接口或重复启动轮询），
  `terminate()` 幂等且容忍轮询任务异常与会话关闭失败，取消时正确向上传播 `CancelledError`。
- **插件页面**：新增 `views/overview` 概览视图（自包含、无外部资源）与
  `.astrbot-plugin/i18n/{zh-CN,en-US}.json` 多语言文案，遵循 AstrBot 4.29 插件页面契约。
- **发布资产**：新增 `logo.png`（512×512 PNG，< 1 MB）、`SECURITY.md`、`CONTRIBUTING.md`
  与 `scripts/check_release.py`（发布前一键门禁）。
- **版本兼容**：声明的最低版本 AstrBot 4.18 通过独立 CI 任务真实安装并跑通运行时测试（565 项）；
  在没有 `astrbot.api.web` 的 4.18~4.28 上，插件 Web API 返回与 4.29 完全同构的
  `{"status": "ok", "data": {...}}`，调用方无需区分版本。
- **运行环境下限**：AstrBot ≥ 4.18 的 `Requires-Python` 为 `>=3.12`，因此 Python 下限、CI 矩阵、
  ruff `target-version` 与 mypy `python_version` 统一为 3.12（并由合规测试锁定一致性）。

### 工程化

- 测试：新增 `tests/test_compliance.py`（元数据 / 配置 schema / i18n / 插件页面 / 发布资产合规）、
  `tests/test_helpers_security.py`（脱敏与输入校验）与 `tests/test_main_hardening.py`
  （节流、上限、统计、健康快照、Web API、生命周期）；测试数达 **599**，整体覆盖率 **99.9%**。
- 静态检查：ruff 规则集扩展到 `E,F,W,I,UP,B,C4,SIM,RUF,PIE,RET,BLE,ASYNC` 并强制 `ruff format`；
  新增 mypy 配置（5 个源码模块零错误）。
- CI：新增 `typecheck`、`compliance` 与 `compat-min-astrbot`（AstrBot 4.18 最低版本兼容）任务，
  `test` 任务在 Python 3.12 / 3.13 上运行并校验徽章一致性（`python scripts/gen_badge.py --check`）。
- 新增 `.pre-commit-config.yaml`（ruff / mypy / 元数据 / 合规 / 页面冒烟 / 徽章）。

## [v0.2.1]

- 初始公开版本：B站链接解析、UP主动态订阅、直播推送、过滤器与视频下载。
