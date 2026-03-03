# astrbot-plugin-bili-all-function

一个聚焦 B 站的 AstrBot 插件，当前包含：

- 自动解析 B 站链接 / `BV` / `av` 号并返回视频关键信息
- 单一入口统一解析普通消息与 QQ 小程序 JSON 卡片中的 B 站视频链接
- 可选自动下载视频到本地暂存并直接发送到 QQ（支持画质、时长、大小限制）
- 命中解析后会停止事件传播（`event.stop_event()`），避免继续调用 AstrBot 模型提供商产生额外费用
- 订阅 UP 主动态并后台轮询推送
- 直播开播 / 下播状态推送

## 指令

- `/bili_sub <uid> [过滤器...]` 订阅 UP 主动态（可更新过滤条件）
- `/bili_sub_del <uid>` 取消订阅
- `/bili_sub_list` 查看当前会话订阅列表
- `/bili_sub_test <uid> [过滤器...]` 测试抓取与过滤效果（不保存订阅）
- `/bili_state` 自检插件状态（先检查配置，再检查 Cookie 与订阅接口可用性）
- `/bili_global_sub <会话UMO> <uid> [过滤器...]` 管理员为指定会话添加订阅
- `/bili_global_del <会话UMO> [uid]` 管理员删除指定会话订阅（可删全部）
- `/bili_global_list` 管理员查看全局订阅

## 过滤器

支持以下类型过滤：

- `forward` 过滤转发动态
- `lottery` 过滤互动抽奖动态
- `video` 过滤视频动态
- `article` 过滤专栏动态
- `draw` 过滤图文/文字动态
- `live` 过滤直播开播/下播推送
- `forward_lottery` 过滤“转发的互动抽奖”

除以上关键字外，其它参数会被当作正则表达式，用于过滤动态文本。

## 说明

- 订阅数据保存于 `data/subscriptions.json`
- 轮询采用 round-robin（每轮仅检测 1 个 UID），间隔默认 `300` 秒，可通过配置 `poll_interval_seconds` 调整
- 请求超时默认 `15` 秒，可通过配置 `timeout_seconds` 调整
- 每次最多推送 `3` 条新动态，可通过配置 `dynamic_limit` 调整
- 管理员指令依赖配置项 `admin_ids`（数组，元素为发送者ID）
- 视频下载暂存目录为 `data/cache/videos`，可配置发送后自动删除
- 动态订阅固定使用 `desktop/v1/feed/space` 稳定源，遇到 `HTTP 412` 时静默降级并等待下一轮重试

## 配置页面

本插件已提供 WebUI 配置 Schema 文件：`_conf_schema.json`。

可在 AstrBot 插件管理页面直接配置：

- `features.enable_bili_parse`：是否启用普通 B 站链接解析
- `features.enable_bili_parse`：是否启用统一入口解析（文本 + QQ 小程序 JSON）
- `subscription.*`：轮询周期、超时、推送上限、任务间隔、去重缓存长度
- `subscription.http_retry_count`：网络请求失败时的重试次数
- `subscription.http_retry_delay_seconds`：网络请求重试基础间隔
- `video_download.enable`：是否启用“下载后发送视频文件”
- `video_download.quality`：视频目标画质（自动向下兼容）
- `video_download.max_duration_seconds`：可暂存视频的最大时长
- `video_download.max_size_mb`：可暂存视频的最大体积
- `video_download.auto_delete`：发送后是否删除本地暂存文件
- `video_download.bili_cookie`：B站登录 Cookie（可选，用于解锁更高画质下载）
- `permission.admin_ids`：管理员 ID（逗号/空格/换行分隔）

> 提示：如需 1080P+/4K 等更高画质，通常需要在 `video_download.bili_cookie` 中填写有效登录态 Cookie（建议包含 `SESSDATA`）。

## Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)

## 代码结构

- `main.py`：插件入口、指令与事件处理
- `bili_constants.py`：B站正则、过滤类型、画质映射常量
- `bili_models.py`：数据模型（`Subscription`）
- `bili_helpers.py`：纯工具函数（链接提取、格式化、动态文本解析、过滤参数解析）
- `bili_logic.py`：业务逻辑函数（视频/动态消息构建、动态过滤、订阅映射与去重维护）

## 致谢

- [vacacia/astrbot_plugin_link_resolver](https://github.com/vacacia/astrbot_plugin_link_resolver)（B站链接解析思路）
- [Soulter/astrbot_plugin_bilibili](https://github.com/Soulter/astrbot_plugin_bilibili)（UP主动态订阅与轮询推送思路）
