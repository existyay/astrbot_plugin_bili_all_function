# astrbot-plugin-bili-all-function

<div align="center">

![coverage](docs/badges/coverage.svg)
![version](https://img.shields.io/badge/version-v0.4.0-blue)
![license](https://img.shields.io/badge/license-AGPL--3.0-orange)
![astrbot](https://img.shields.io/badge/AstrBot-%3E%3D4.18-ff69b4)
![ruff](https://img.shields.io/badge/lint-ruff-261230)
![mypy](https://img.shields.io/badge/types-mypy-blue)
![tests](https://img.shields.io/badge/tests-687%20passed-brightgreen)

一个聚焦 **B站（bilibili）** 的 AstrBot 插件：链接解析 + UP主动态订阅 + 直播推送。

</div>

## 功能特性

- **一键解析视频链接**：统一解析普通文本与 QQ 小程序 JSON 卡片中的 `BV号` / `av号` /
  `bilibili.com/video/...` / `b23.tv` 短链，回复标题、UP主、播放量、点赞、时长、简介与链接。
- **UP主动态订阅**：后台轮询订阅的 UP 主，有新动态时主动推送（含正文、配图、视频封面与跳转链接）。
- **多类型专属排版**：视频 / 图文 / 文字 / 转发 / 专栏 / 直播 / 合集 / 音乐 / 番剧 / 课程等动态分别排版，
  自动补全卡片标题、简介、播放量、阅读量、人气等字段，卡片字段缺失也不会输出 `{...}` 原始数据。
- **自动换行 + 纯文本输出**：按显示列宽（一个汉字算 2 列）自动换行，链接与 `@用户` 不会被拆开；
  推送内容统一去除 emoji 与 B站表情 token（如 `[tv_微笑]`），保证各平台客户端显示一致。
- **直播状态推送**：开播 / 下播自动提醒，开播附带直播间标题与封面。
- **灵活的过滤器**：支持按 `转发 / 抽奖 / 视频 / 专栏 / 图文 / 直播` 类型过滤，也支持正则过滤，
  可以只接收你关心的内容。
- **持久化与去重**：订阅信息落盘保存（原子写入 + `__schema__` 版本迁移），动态 ID 采用 LRU 去重缓存，避免重复推送。
- **可选视频下载**：可将解析到的视频下载后直接发送到聊天（支持画质降级、时长/大小限制、自动清理缓存）。
- **LLM 函数工具**：内置 `bili_video_info` 工具，大模型可在对话中直接查询 B站视频信息。
- **插件页面**：Dashboard 内嵌 `views/overview` 视图，可视化查看运行状态、订阅量与请求统计。
- **可观测性**：`/bili_state` 一键自检，并输出请求、推送、下载等运行统计；内置健康快照 API。
- **安全加固**：Cookie 出网域名白名单 + 全链路脱敏 + 输入长度/正则护栏（见 [SECURITY.md](./SECURITY.md)）。
- **完整测试**：687 个离线用例，语句覆盖率 100%、整体覆盖率约 99.97%，并带 AstrBot 合规自检。

## 兼容性

| 项目 | 说明 |
| --- | --- |
| AstrBot | `>= 4.18`（已适配 4.29 的插件规范：不再使用已废弃的 `@register`，元数据由 `metadata.yaml` 提供，插件页面走 `views/` 约定） |
| 平台 | `aiocqhttp`（OneBot v11 / QQ） |
| Python | 3.12+（AstrBot ≥ 4.18 的 `Requires-Python` 即为 `>=3.12`，插件随之以此为下限） |

> 插件会对宿主能力做**能力探测**：`register_web_api`、插件页面、i18n 等接口不存在时自动跳过，
> 因此在 4.18 与 4.29 上均可正常加载。

## 安装

1. 将本仓库目录放入 AstrBot 的 `data/plugins/` 下，或在插件市场中搜索 `bili_all` 安装；
2. 安装依赖（一般已内置）：`pip install -r requirements.txt`；
3. 在 AstrBot 面板中重载插件，然后发送 `/bili_state` 检查是否正常。

## 指令一览

| 指令 | 说明 |
| --- | --- |
| `/bili_sub <UID> [过滤器...]` | 订阅 UP 主动态（UID 为 B站数字 UID） |
| `/bili_sub_del <UID>` | 取消订阅 |
| `/bili_sub_list` | 查看当前会话的订阅列表 |
| `/bili_sub_test <UID> [过滤器...]` | 立即抓取一次最新动态，用于验证过滤规则 |
| `/bili_state` | 插件自检：配置、Cookie、订阅链路与功能开关 |
| `/bili_global_sub <会话UMO> <UID> [过滤器...]` | 【管理员】为指定会话订阅 |
| `/bili_global_del <会话UMO> [UID]` | 【管理员】删除指定会话的订阅（省略 UID 表示删除全部） |
| `/bili_global_list` | 【管理员】查看全部会话的订阅 |

> 管理员指令对 **AstrBot 原生管理员** 与配置项 `permission.admin_ids` 中的账号同时生效。

### 过滤器

直接在指令后面追加关键字即可，可混用多个：

```text
/bili_sub 703007996 video            # 只接收该 UP 的视频动态
/bili_sub 703007996 forward_lottery  # 只过滤掉转发抽奖
/bili_sub 703007996 限定|预约        # 正则：内容命中则跳过
```

| 过滤器 | 含义 |
| --- | --- |
| `forward` | 过滤全部转发动态 |
| `forward_lottery` | 只过滤「转发抽奖」类动态 |
| `lottery` | 过滤抽奖类动态 |
| `video` | 过滤视频投稿动态 |
| `article` | 过滤专栏文章 |
| `draw` | 过滤图文动态 |
| `live` | 不推送该 UP 的直播状态变化 |

非上述关键字的参数会被当作 **正则表达式**，命中动态正文时跳过该条推送。

## 配置说明

在 AstrBot 插件配置页中可视化编辑，配置项定义见 [`_conf_schema.json`](./_conf_schema.json)。

<details>
<summary><b>features —— 功能开关</b></summary>

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_bili_parse` | `true` | 启用B站链接解析 |
| `send_video_info` | `true` | 解析后回复视频信息 |
| `enable_llm_tool` | `true` | 注册 `bili_video_info` 函数工具 |

</details>

<details>
<summary><b>subscription —— 订阅轮询</b></summary>

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `poll_interval_seconds` | `300` | 轮询周期（建议 ≥ 60 秒，过低易触发风控） |
| `timeout_seconds` | `15` | 单次请求超时 |
| `http_retry_count` | `2` | 解析链路的轻量重试次数（轮询固定不重试） |
| `http_retry_delay_seconds` | `0.35` | 重试基础间隔，实际等待 = 基础间隔 × 轮次 |
| `dynamic_limit` | `3` | 每轮每订阅最多推送的未读动态数（始终优先推送最新一条） |
| `uid_concurrency` | `3` | 同一轮并发检测的 UID 数量 |
| `uid_task_gap_seconds` | `1` | 每个 UID 检测后的间隔 |
| `recent_ids_limit` | `20` | 动态去重缓存长度 |
| `empty_retry_count` | `3` | 动态接口返回空列表时的重试次数（**建议保持 > 0**） |
| `empty_retry_delay_seconds` | `1.0` | 空响应重试基础间隔，实际等待 = 基础间隔 × 轮次 |
| `min_request_interval_seconds` | `0.2` | 两次外部请求之间的最小间隔（全局节流，降低风控概率） |
| `wrap_width` | `60` | 推送文本自动换行的显示列宽（一个汉字算 2 列），`0` 表示不换行 |

</details>

<details>
<summary><b>video_download —— 视频下载</b></summary>

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable` | `false` | 启用「下载视频后发送」 |
| `quality` | `720P` | 目标画质（`360P/480P/720P/1080P/1080P+/1080P60/4K`，会逐级降级尝试） |
| `max_duration_seconds` | `600` | 时长上限，`0` 表示不限制 |
| `max_size_mb` | `100` | 大小上限，`0` 表示不限制 |
| `retry_count` | `2` | 下载重试次数 |
| `auto_delete` | `true` | 发送后删除本地缓存 |
| `bili_cookie` | 空 | B站登录 Cookie（含 `SESSDATA` 可解锁 1080P 及以上） |

</details>

<details>
<summary><b>permission —— 权限</b></summary>

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `admin_ids` | 空 | 允许使用管理员指令的账号 ID，支持逗号 / 空格 / 换行分隔 |

</details>

## 插件页面与运行状态

插件内置一个 Dashboard 视图（`views/overview/index.html`），在 AstrBot 管理面板的插件详情页中打开即可看到：

- 运行状态（轮询是否存活、运行时长、订阅会话数与订阅总数）；
- 请求统计（总请求、失败、静默风控 412、空响应）；
- 推送统计（动态推送、直播推送、发送失败）与下载量；
- 当前轮询策略（间隔、并发、节流、动态条数上限、换行列宽）与功能开关；
- 数据版本（`subscriptions.json` 的 `__schema__`）与 Cookie 脱敏提示。

视图完全自包含（内联 CSS/JS，无外部 CDN 请求），通过 AstrBot 官方 Bridge SDK 获取上下文与调用接口；
文案位于 `.astrbot-plugin/i18n/{zh-CN,en-US}.json`，随面板语言自动切换。

同时插件注册了一个只读的插件 Web API（`GET /api/v1/plugins/extensions/astrbot_plugin_bili_all_function/status`），
返回与页面同源的 JSON 健康快照，便于接入外部监控：

```json
{
  "status": "ok",
  "data": {
    "plugin": "astrbot_plugin_bili_all_function",
    "display_name": "bili_all",
    "version": "0.4.0",
    "uptime_seconds": 812,
    "healthy": true,
    "poll_running": true,
    "sessions": 2,
    "subscriptions": 3,
    "schema_version": 2,
    "cookie_configured": true,
    "cookie_masked": "SESSDATA=abcd***",
    "features": {"parse": true, "video_info": true, "download": false, "llm_tool": true},
    "poll": {
      "interval_seconds": 300,
      "concurrency": 3,
      "dynamic_limit": 3,
      "min_request_interval_seconds": 0.2,
      "wrap_width": 60
    },
    "stats": {
      "http_requests": 128, "http_failures": 0, "http_silent": 2,
      "poll_cycles": 41, "poll_errors": 0,
      "dynamic_pushes": 7, "live_pushes": 2,
      "downloads": 0, "download_bytes": 0, "send_failures": 0
    }
  }
}
```

> 返回值中**不会**出现完整 Cookie；该接口挂载在 AstrBot 面板的 `/api/v1/plugins/extensions/...` 下，
> 沿用面板的登录态与插件权限校验。AstrBot 4.18~4.28 没有 `astrbot.api.web`，此时接口会返回
> **完全同构的 dict**（同样是 `{"status": "ok", "data": {...}}`），调用方无需区分版本。

## 安全与隐私

- **Cookie 只发往 B站 域名**：带 Cookie 的请求会经过域名白名单校验，重定向到白名单外时自动剥离 Cookie；
- **全链路脱敏**：日志、`/bili_state` 与插件页面只显示 `SESSDATA=abcd***` 形式的掩码；
- **不落盘凭据**：`subscriptions.json` 仅保存 UID / 锚点 / 过滤器 / 去重缓存（带 `__schema__` 版本号）；
- **输入护栏**：UID 范围、正则长度与数量、消息扫描长度、响应体大小、下载文件名均有上限。

完整说明与漏洞报告渠道见 [SECURITY.md](./SECURITY.md)。

## 质量与合规

| 检查 | 命令 | 现状 |
| --- | --- | --- |
| 单元测试 + 覆盖率门禁（≥95%） | `pytest` | 687 passed，99.97% |
| 最低支持版本兼容性 | `pytest tests --ignore=tests/test_compliance.py`（对 AstrBot 4.18） | 652 passed |
| 静态检查（ruff，生产级规则集） | `ruff check .` | 通过 |
| 代码风格 | `ruff format --check .` | 通过 |
| 类型检查（mypy） | `mypy` | 通过（5 个源码模块，零错误） |
| AstrBot 安装/市场合规 | `pytest tests/test_compliance.py` | 通过（元数据、schema、i18n、插件页面、发布资产） |
| 插件页面渲染冒烟（node） | `node scripts/check_view.mjs` | 通过（内联脚本语法 + 真实语言包渲染） |
| 元数据与配置一致性 | `python scripts/check_metadata.py` | 通过 |
| 发布前一键门禁 | `python scripts/check_release.py` | 通过 |

合规自检覆盖 AstrBot 4.29 的真实加载契约：

- `metadata.yaml` 必填字段、`astrbot_version` 的 PEP 440 约束、目录名与插件名一致、可导入名合法；
- `_conf_schema.json` 能被 `PluginManager` 正常解析，分组/类型/提示/默认值与运行时默认值一致；
- `.astrbot-plugin/i18n/` 的 `zh-CN` / `en-US` 键集合完全一致，且视图用到的文案键全部存在；
- `views/overview/index.html` 能被 `PluginPageService` 发现、不引用任何外部资源，
  且页面读取的字段都能在真实健康快照中找到对应项；
- `logo.png` 为合法 PNG 且小于 1 MB；`README` / `CHANGELOG` / `LICENSE` / `SECURITY.md` /
  `CONTRIBUTING.md` / `requirements.txt` 齐备，依赖均有版本约束；
- 仓库不含明文 Cookie、Token 或本地绝对路径，运行期产物未被纳入版本库。

## 推送效果

推送文本按**显示列宽**自动换行（`subscription.wrap_width`，默认 60 列，填 `0` 关闭），
长链接不会被拆断；内容中的 emoji 与 B站表情 token 会被清洗为纯文本。

视频动态（头像/标题 → 统计 → 封面 → 链接）：

```text
[B站动态] 嘉然今天吃什么 · 视频动态
闪身步实用教程
上次教完了，这次该实践了，大家学会了没有？
播放 2.5万 | 弹幕 231 | 时长 00:29
<视频封面图>
视频：https://www.bilibili.com/video/BV1zKJQ6fEaY
动态：https://t.bilibili.com/1259695031548117009
```

图文 / 长文动态（正文按列宽换行，空行保留）：

```text
[B站动态] A-SOUL_Official · 图文动态
时间流转，与你的心意依旧在舞台灯光亮起的此刻共鸣
本次【A-SOUL此间共鸣演唱会】流程安排及场贩情报公开，请小伙伴
们仔细阅读！
10月24日-10月25日，和小伙伴们不见不散~
<配图 1..9>
动态：https://t.bilibili.com/1259699575671750656
```

转发动态（先给转发理由，再给原文摘要与原动态数据）：

```text
[B站动态] A-SOUL_Official · 转发动态
真心驱散乌云后，就能看到舞台中心闪耀的贝拉~
@贝拉kira: 遥远闪烁的，是《雨季后的星光》| 贝拉个人单曲试听
新单曲将在10.24-10.25的A-SOUL 此间共鸣线下演出中首次表演
和贝拉一起迎接雨季后的星光吧~
原动态数据：播放 1.3万 | 弹幕 136 | 时长 00:43
<封面图>
原视频：https://www.bilibili.com/video/BV1Hw4m1r7dp
动态：https://t.bilibili.com/1259646803390758934
```

专栏 / 直播推荐类卡片（标题 + 摘要 + 统计 + 对应链接）：

```text
[B站动态] 某某UP主 · 专栏文章
《一些关于新版本的碎碎念》
这次更新主要调整了排版与过滤逻辑……
阅读 2.0万
专栏：https://www.bilibili.com/read/cv123456
动态：https://t.bilibili.com/1259695031548117009
```

```text
[B站直播] 嘉然今天吃什么 开播了
【突击】一起看jojo吧~
<直播封面图>
直播：https://live.bilibili.com/22637261
```

排版顺序固定为 **头部 → 正文（含卡片标题/简介与统计、转发的原文摘要）→ 图片 → 链接**，
既保证正文不会被多图挤出屏幕，也保证最新动态永远出现在推送批次中。

链接块规则：

- 标签统一为中文（`视频：` / `直播：` / `专栏：` / `合集：` / `动态：` / `原视频：` / `原文：`），
  不使用 emoji 前缀；
- **按内容去重**：同一内容的 `t.bilibili.com/<动态ID>` 与 `opus/<动态ID>`、
  带 `?spm_id_from=…` 的同一视频链接、合集 `sid` 等只会保留一条；
- **不重复正文里的链接**：正文（含转发的原文）已经出现过的链接不会再在链接块里列一遍；
- **单条推送最多 2 行链接**，内容链接（视频/专栏/直播…）优先，其次才是「动态 / 原文」这类跳转页，
  因此不会出现一条动态刷出三四行链接的情况。

## 实现要点

- **接口选择**：动态使用桌面端接口
  `x/polymer/web-dynamic/desktop/v1/feed/space`（无需 WBI 签名）；旧的
  `web-dynamic/v1/feed/space` 在高频请求下会直接返回 `HTTP 412`，因此已不再使用。
- **空响应重试（关键）**：该接口会**随机返回 `code=0` 但 `items=[]`**（实测同一 UID
  连续请求成功率只有 6~7 成，与 Cookie/参数无关，疑似 CDN 缓存与风控边缘节点），
  且失败时不会更新订阅锚点，因此插件会按 `empty_retry_count` 重试、
  每次重试都带一个时间戳参数绕开空缓存。这是「订阅收不到最新动态」的另一个主要原因。
- **置顶识别**：新版接口把置顶标记放在 `modules.module_author.is_top`。
  置顶动态不参与「新动态」判定，否则订阅锚点会被钉在旧动态上，导致**最新动态永远推不出来**。
- **最新优先**：每轮先把所有未读动态标记为已读（避免重复轰炸），
  再按时间正序推送其中**最新**的 `dynamic_limit` 条。
- **排版修复**：新版接口中只有 `MODULE_TYPE_DESC` 的动态（无 `module_dynamic`）
  已能被正确规范化，正文不会再丢失；`2.5万` 这类计数也能安全解析。
- **排版与清洗**：正文按显示列宽自动换行（含基本「行头禁则」，标点不会落到行首），
  链接、`@用户`、`#话题#` 视为整体不拆断；渲染前统一清洗 Unicode emoji 与 B站表情 token，
  并把 `{"text": ...}` 这类字典字段安全地拆成文本，避免把原始 JSON 发到群里。

## 开发与测试

```bash
python -m venv .venv && . .venv/Scripts/activate      # Windows
pip install -r requirements.txt -r requirements-dev.txt
pre-commit install                                    # 可选：提交前自动跑同一套检查

pytest                        # 运行测试（默认开启覆盖率，低于 95% 直接失败）
ruff check . && ruff format --check .   # 静态检查 + 风格检查
mypy                          # 类型检查
python scripts/check_release.py       # 发布前一键门禁（= CI 全部检查）
node scripts/check_view.mjs   # 插件页面语法 + 渲染冒烟（需 node ≥ 18）
python scripts/gen_badge.py   # 重新生成 docs/badges/coverage.svg（--check 只校验）
python scripts/refresh_fixtures.py   # 联网刷新 tests/fixtures 下的真实接口快照
python scripts/live_check.py 946974  # 联网联调：真实拉取动态并打印推送排版
```

- `tests/` 覆盖常量、工具函数、业务逻辑、HTTP 层、下载链路、持久化与全部指令/事件 handler，
  测试全程**离线**运行（HTTP 通过替身注入）；
- `tests/test_compliance.py` 面向 AstrBot 安装与市场准入，直接调用 4.29 的
  `PluginManager` / `PluginPageService` 等真实实现做契约校验；
- `tests/fixtures/` 是从线上接口抓取并裁剪后的真实快照；
- 目录结构与贡献流程见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

发布流程：打 `v*` 标签即触发 [release.yml](./.github/workflows/release.yml)，
它会重跑全部门禁、校验标签与 `metadata.yaml` 版本一致，并产出插件压缩包与 Release。

## 常见问题

<details>
<summary>订阅后收不到最新动态？</summary>

v0.3.0 起已修复该问题（置顶动态锚点错误 + 未回溯最新动态）。
请升级后删除并重新订阅一次，让锚点重新对齐。
若仍异常，请先执行 `/bili_sub_test <UID>` 看能否抓到最新动态。
</details>

<details>
<summary>日志出现 HTTP 412 / 动态为空？</summary>

属于 B站风控与接口抖动表现：

- 旧接口 `web-dynamic/v1/feed/space` 会直接 412，插件已改用桌面端接口；
- 桌面端接口本身也会随机返回空列表（实测成功率约 6~7 成），
  插件已内置 `empty_retry_count` 重试（默认 3 次）且失败时不更新锚点，下一轮会继续补推；
- 若日志频繁出现「连续 N 次返回空结果」，请把 `poll_interval_seconds` 调大（≥ 300 秒）、
  `uid_concurrency` 调小，避免被判定为爬虫。
</details>

<details>
<summary>下载只有低画质？</summary>

在 `video_download.bili_cookie` 中填入浏览器登录态 Cookie（至少包含 `SESSDATA`），
再用 `/bili_state` 确认 Cookie 检查为通过。
</details>

## 更新日志

见 [CHANGELOG.md](./CHANGELOG.md)。

## 参与贡献

欢迎提交 Issue 与 PR，请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)（环境搭建、代码规范、测试要求）。
安全相关问题请按 [SECURITY.md](./SECURITY.md) 私下报告。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [Soulter/astrbot_plugin_bilibili](https://github.com/Soulter/astrbot_plugin_bilibili)（UP主动态订阅与轮询推送思路）
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)（接口文档）

## License

[AGPL-3.0](./LICENSE)
