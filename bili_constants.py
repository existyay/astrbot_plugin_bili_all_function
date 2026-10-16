"""B站相关常量、接口地址与预编译正则。

本模块不导入 ``astrbot``，因此可以被测试与被其它模块安全复用。
"""

from __future__ import annotations

import re

# ───────────────────────── 插件元信息 ─────────────────────────
#: 插件唯一识别名，与 metadata.yaml 中的 name 保持一致（用于数据目录定位）。
PLUGIN_NAME = "astrbot_plugin_bili_all_function"
PLUGIN_AUTHOR = "existyay"
PLUGIN_DISPLAY_NAME = "bili_all"
PLUGIN_VERSION = "0.3.1"
PLUGIN_REPO = "https://github.com/existyay/astrbot_plugin_bili_all_function"
PLUGIN_DESC = "B站链接解析 + UP主动态订阅 + 直播推送。"

# ───────────────────────── 原始正则模式 ─────────────────────────
BILI_BV_PATTERN = r"\bBV[0-9A-Za-z]{10}\b"
BILI_AV_PATTERN = r"\bav\d+\b"
BILI_SHORT_LINK_PATTERN = r"https?://(?:b23\.tv|bili2233\.cn)/[^\s'\"<>]+"
BILI_VIDEO_LINK_PATTERN = r"https?://(?:www\.|m\.)?bilibili\.com/video/[^\s'\"<>]+"
BILI_MESSAGE_PATTERN = (
    rf"(?:{BILI_BV_PATTERN}|{BILI_AV_PATTERN}"
    rf"|{BILI_SHORT_LINK_PATTERN}|{BILI_VIDEO_LINK_PATTERN})"
)

# ───────────────────────── 预编译正则（热路径使用） ─────────────────────────
RE_BV = re.compile(BILI_BV_PATTERN)
RE_AV = re.compile(BILI_AV_PATTERN, re.IGNORECASE)
RE_SHORT_LINK = re.compile(BILI_SHORT_LINK_PATTERN, re.IGNORECASE)
RE_VIDEO_LINK = re.compile(BILI_VIDEO_LINK_PATTERN, re.IGNORECASE)
RE_MESSAGE = re.compile(BILI_MESSAGE_PATTERN, re.IGNORECASE)
RE_URL = re.compile(r"https?://[^\s'\"<>]+")
RE_LOTTERY = re.compile(r"恭喜.*等\d+位同学中奖")

#: 快速排除：文本中不包含以下任一关键字时无需做正则扫描。
FAST_KEYWORDS: tuple[str, ...] = (
    "bilibili.com/video/",
    "b23.tv/",
    "bili2233.cn/",
    "bv",
    "av",
)

# ───────────────────────── 过滤类型 ─────────────────────────
VALID_FILTER_TYPES: frozenset[str] = frozenset(
    {"forward", "lottery", "video", "article", "draw", "live", "forward_lottery"}
)

# ───────────────────────── 画质映射 ─────────────────────────
QUALITY_TO_QN: dict[str, int] = {
    "360P": 16,
    "480P": 32,
    "720P": 64,
    "1080P": 80,
    "1080P+": 112,
    "1080P60": 116,
    "4K": 120,
}
DEFAULT_QUALITY_LABEL = "720P"
DEFAULT_QN = QUALITY_TO_QN[DEFAULT_QUALITY_LABEL]

# ───────────────────────── B站接口 ─────────────────────────
API_VIDEO_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
#: 桌面端动态接口：返回 ``modules`` 数组的新结构，且无需 wbi 签名。
API_DYNAMIC_FEED = "https://api.bilibili.com/x/polymer/web-dynamic/desktop/v1/feed/space"
API_LIVE_STATUS = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"
API_LIVE_ROOM = "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld"
DYNAMIC_SPACE_URL = "https://space.bilibili.com/{uid}/dynamic"
DYNAMIC_DETAIL_URL = "https://t.bilibili.com/{dyn_id}"
VIDEO_DETAIL_URL = "https://www.bilibili.com/video/{bvid}"
LIVE_ROOM_URL = "https://live.bilibili.com/{room_id}"

# ───────────────────────── HTTP / 业务常量 ─────────────────────────
#: 可安全重试的 HTTP 状态码。
RETRYABLE_HTTP: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
#: 动态接口命中风控时静默降级（不打印 WARNING 噪音）。
DYNAMIC_SILENT_CODES: frozenset[int] = frozenset({412})
#: 动态接口的 ``features`` 参数（与 web 端动态页保持一致）。
DYNAMIC_FEED_FEATURES = "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote"
#: B站新版置顶动态在 ``module_tag.text`` 中的标记。
DYNAMIC_TOP_TAG = "置顶"

#: 动态正文/转发摘要截断长度。
DYNAMIC_TEXT_LIMIT = 300
FORWARD_TEXT_LIMIT = 120
VIDEO_DESC_LIMIT = 120
#: 单条动态最多推送的图片数量。
DYNAMIC_IMAGE_LIMIT = 9

DYNAMIC_TYPE_LABELS: dict[str, str] = {
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_WORD": "文字动态",
    "DYNAMIC_TYPE_AV": "视频动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏文章",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播推荐",
}
DEFAULT_DYNAMIC_TYPE_LABEL = "动态"

#: 配置默认值（同时用于 _conf_schema.json 与运行时兜底）。
DEFAULT_POLL_INTERVAL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_DYNAMIC_LIMIT = 3
DEFAULT_UID_TASK_GAP_SECONDS = 1
DEFAULT_UID_CONCURRENCY = 3
DEFAULT_RECENT_IDS_LIMIT = 20
DEFAULT_EMPTY_RETRY_COUNT = 3
DEFAULT_EMPTY_RETRY_DELAY_SECONDS = 1.0
DEFAULT_HTTP_RETRY_COUNT = 2
DEFAULT_HTTP_RETRY_DELAY_SECONDS = 0.35
DEFAULT_VIDEO_MAX_DURATION_SECONDS = 600
DEFAULT_VIDEO_MAX_SIZE_MB = 100
DEFAULT_VIDEO_RETRY_COUNT = 2
#: 两次外部请求之间的最小间隔（秒），用于降低触发 B站风控的概率。
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 0.2

# ───────────────────────── 安全与资源上限 ─────────────────────────
#: 统一 UA（带浏览器标识，降低被判定为脚本爬虫的概率）。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
#: 允许携带登录 Cookie 的域名后缀（避免 Cookie 泄漏到第三方重定向目标）。
BILI_COOKIE_HOST_SUFFIXES: tuple[str, ...] = (
    "bilibili.com",
    "biliapi.net",
    "bilibili.tv",
    "bilivideo.com",
    "bilivideo.cn",
    "hdslb.com",
    "b23.tv",
    "bili2233.cn",
)
#: Cookie 中一旦出现这些片段就必须脱敏后再输出到日志/消息。
SENSITIVE_COOKIE_KEYS: tuple[str, ...] = (
    "SESSDATA",
    "bili_jct",
    "DedeUserID__ckMd5",
    "buvid3",
    "buvid4",
    "access_key",
    "refresh_token",
)
#: 单个 HTTP 响应体的最大读取字节数（防止异常响应耗尽内存）。
MAX_JSON_BYTES = 4 * 1024 * 1024
#: 单条消息参与正则扫描的最大长度（防止超长文本拖慢事件处理）。
MAX_MESSAGE_SCAN_LEN = 4000
#: LLM 工具入参长度上限。
MAX_LLM_URL_LEN = 2000
#: 用户自定义正则的长度与数量上限（防止 ReDoS 与配置爆炸）。
MAX_FILTER_REGEX_LEN = 200
MAX_FILTER_REGEX_COUNT = 10
#: 单个会话最多订阅的 UP 数 / 最多维护的会话数 / 合法 UID 上限。
MAX_SUBS_PER_SESSION = 50
MAX_SESSIONS = 500
MAX_UID = 10**12
#: 下载分片大小与文件名清理保留字符。
DOWNLOAD_CHUNK_SIZE = 256 * 1024
SAFE_FILENAME_RE = re.compile(r"[^0-9A-Za-z._-]+")
#: 订阅数据文件的结构版本（便于后续迁移）。
SUBSCRIPTIONS_SCHEMA_VERSION = 2
#: 订阅数据文件中记录结构版本的保留键（加载时会被剔除）。
SUBSCRIPTIONS_SCHEMA_KEY = "__schema__"

# ───────────────────────── 插件包内约定（AstrBot 插件规范） ─────────────────────────
#: 插件 Logo 文件名，AstrBot 面板/云端插件卡片会自动读取该文件。
LOGO_FILE_NAME = "logo.png"
#: 插件国际化与页面资源目录（AstrBot 会读取 ``<dir>/i18n/<locale>.json``）。
PLUGIN_RESOURCE_DIR_NAME = ".astrbot-plugin"
#: 插件页面的根目录名（``views`` 为首选，``pages`` 为兼容别名）。
VIEWS_DIR_NAME = "views"
#: 插件页面入口文件名。
VIEW_ENTRY_FILE_NAME = "index.html"
#: 插件自带 Web API 的子路由（供插件页面调用）。
WEB_API_STATUS_ROUTE = f"/{PLUGIN_NAME}/status"
