# 安全策略（Security Policy）

本插件会代表使用者访问 B站接口，并可能在配置中保存 B站登录 Cookie。
我们把它当作**处理凭据的软件**来维护，因此对输入校验、出网目标与日志脱敏都有明确约束。

## 支持版本

| 版本 | 是否接收安全修复 |
| --- | --- |
| `v0.3.x`（当前主线） | ✅ |
| `v0.2.x` 及更早 | ❌ 请先升级 |

## 报告漏洞

请**不要**通过公开 Issue 披露安全问题。请任选一种私下渠道：

1. 使用 GitHub 的 [Private vulnerability reporting](https://docs.github.com/zh/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)（仓库 **Security → Report a vulnerability**）；
2. 若无法使用上述渠道，请在 Issue 中仅留下「希望私下沟通安全问题」的联系方式，不要在 Issue 正文中给出复现细节。

请尽量提供：

- 受影响版本（`metadata.yaml` 中的 `version`）与 AstrBot 版本；
- 复现步骤或最小 PoC；
- 影响评估（信息泄露 / 越权 / 服务端请求伪造 / 拒绝服务等）；
- 若有修复建议，也欢迎一并给出。

我们会在 **72 小时内**确认收到，并在确认后 **7 天内**给出修复计划；
修复发布后会在 [CHANGELOG.md](./CHANGELOG.md) 中致谢（除非你希望匿名）。

## 设计上的安全约束

### 凭据（Cookie / Token）

- `video_download.bili_cookie` 仅保存在 AstrBot 的插件配置中，**不会**写入订阅数据文件，
  也不会随任何出网请求发送到非 B站 域名；
- 所有带 Cookie 的请求都会经过域名白名单校验
  （`bilibili.com` / `biliapi.net` / `bilibili.tv` / `bilivideo.com` / `bilivideo.cn` /
  `hdslb.com` / `b23.tv` / `bili2233.cn` 及其子域），
  一旦重定向到白名单之外的域名，Cookie 会被剥离；
- Cookie 中的 `\r` / `\n` / `\0` 会被清理，避免 HTTP 头注入；
- 日志、`/bili_state` 与插件页面只会输出**脱敏后**的 Cookie（形如 `SESSDATA=abcd***`），
  任何情况下都不会回显完整凭据。

### 出网请求

- 仅访问 B站 公开接口；视频下载地址经过 B站 域名校验，防止被重定向到任意地址；
- 响应体有大小上限（默认 4 MB），超出即拒绝，避免恶意/异常响应打爆内存；
- 所有请求走统一的超时、节流（`subscription.min_request_interval_seconds`）与重试退避策略，
  不会对上游造成突发压力。

### 输入处理

- 指令参数、UID、正则过滤器、文件名均有长度与格式校验
  （UID 必须为合法数字且小于 `10^12`；单个正则 ≤ 200 字符、每个订阅 ≤ 10 条；下载文件名会被清洗）；
- 单条消息只扫描前 4000 个字符，LLM 工具入参有长度上限，避免超长输入造成资源占用；
- 订阅数据写入使用「临时文件 + `os.replace`」原子替换，异常中断不会写出半截 JSON。

### 数据存放

| 数据 | 位置 | 说明 |
| --- | --- | --- |
| 订阅数据 | `<插件数据目录>/subscriptions.json` | 仅含 UID、锚点、过滤器与去重缓存，含 `__schema__` 版本号 |
| 视频缓存 | `<插件数据目录>/cache/videos/` | 发送后按配置自动删除 |
| Cookie | AstrBot 插件配置 | 由 AstrBot 自身管理，插件不做额外持久化 |

> 插件不收集任何遥测数据，也不会把使用者信息上传到第三方服务。

## 已知风险与缓解

| 风险 | 缓解措施 |
| --- | --- |
| B站风控（HTTP 412 / 空列表） | 桌面端接口 + 时间戳绕缓存 + 空响应重试 + 请求节流；失败不推进锚点 |
| Cookie 泄露（日志/截图） | 全链路脱敏，插件页面与自检指令只显示 `abcd***` |
| SSRF / 重定向到恶意域名 | 出网域名白名单 + 重定向后剥离 Cookie |
| 正则表达式拒绝服务（ReDoS） | 正则长度与数量上限，单个订阅最多 10 条，编译失败即拒绝保存 |
| 磁盘写坏档 | 原子写入 + `__schema__` 版本迁移 |
| 依赖漏洞 | `requirements.txt` 使用下界约束，CI 全绿后才发布 |

## 使用者建议

- 请使用**小号** Cookie，并定期在 B站 账号安全中心重新登录以失效旧 `SESSDATA`；
- 不要把 `data/` 目录、AstrBot 配置文件或日志公开上传到 Issue / 群聊；
- 若怀疑 Cookie 已泄露，请在 B站 修改密码使 `SESSDATA` 失效，并更新插件配置。
