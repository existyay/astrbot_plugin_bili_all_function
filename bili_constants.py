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
PLUGIN_VERSION = "0.4.0"
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

#: 动态正文自动换行的显示列宽（中文/全角字符按 2 列计），0 表示不换行。
DYNAMIC_TEXT_WRAP_WIDTH = 60
#: 自动换行列宽的允许上限（配置项校验用）。
DYNAMIC_TEXT_WRAP_WIDTH_MAX = 200

#: 正文里的 http(s) 链接（遇到空白、CJK 字符或全角标点即结束）。
RE_URL_IN_TEXT = re.compile(r"https?://[^\s\u3000-\u303f\uff00-\uffef\u4e00-\u9fff]+")

#: 单条推送最多输出的链接行数（内容链接优先，其余按标签优先级截断）。
MAX_PUSH_LINKS = 2
#: 链接内容指纹：把指向同一内容的不同链接形式归并成一个 key。
#: 例：``https://www.bilibili.com/opus/123``、``https://t.bilibili.com/123``、
#: ``https://www.bilibili.com/video/BVxxxx?spm_id_from=...`` 都归并到对应内容。
LINK_KEY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"BV([0-9A-Za-z]{10})", "bv"),
    (r"/video/av(\d+)", "av"),
    (r"t\.bilibili\.com/(\d+)", "dyn"),
    (r"/opus/(\d+)", "dyn"),
    (r"/read/cv(\d+)", "cv"),
    (r"live\.bilibili\.com/(\d+)", "live"),
    (r"/audio/au(\d+)", "au"),
    (r"/bangumi/play/(?:ep|ss)(\d+)", "bangumi"),
    (r"/cheese/play/(?:ep|ss)(\d+)", "cheese"),
    (r"/medialist/detail/ml(\d+)", "ml"),
    (r"[?&]sid=(\d+)", "ml"),
    (r"space\.bilibili\.com/(\d+)", "space"),
)
#: 链接标签优先级（越小越优先保留）：内容类链接先于「本动态 / 原文」这类跳转页。
LINK_LABEL_PRIORITY: dict[str, int] = {"动态": 1, "详情": 1, "原文": 2}
DEFAULT_LINK_PRIORITY = 0

DYNAMIC_TYPE_LABELS: dict[str, str] = {
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_WORD": "文字动态",
    "DYNAMIC_TYPE_AV": "视频动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏文章",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播推荐",
    "DYNAMIC_TYPE_LIVE": "直播动态",
    "DYNAMIC_TYPE_LOTTERY": "互动抽奖",
    "DYNAMIC_TYPE_COMMON_SQUARE": "卡片动态",
    "DYNAMIC_TYPE_MEDIALIST": "合集动态",
    "DYNAMIC_TYPE_MUSIC": "音乐动态",
    "DYNAMIC_TYPE_PGC": "番剧动态",
    "DYNAMIC_TYPE_COURSES": "课程动态",
    "DYNAMIC_TYPE_NONE": "动态",
}
DEFAULT_DYNAMIC_TYPE_LABEL = "动态"

# ───────────────────────── 文本清洗（去 emoji） ─────────────────────────
#: 真 emoji 字符范围（不含 ``→`` 这类普通排版符号，避免误删正文）。
#: 组成：补充符号/表情/交通/扩展 A 区、麻将牌与扑克、杂项符号、装饰符号、
#: 杂项符号与箭头（emoji 呈现部分）、时钟与沙漏、旗帜、变体选择符/零宽连接符/按键帽。
RE_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # 麻将/多米诺/扑克/表情/交通/扩展 A
    "\U00002600-\U000027bf"  # 杂项符号 + 装饰符号（U+2705/U+274C/U+26A0 等）
    "\U00002b00-\U00002bff"  # 杂项符号与箭头（U+2B50/U+2B1B 等 emoji 呈现部分）
    "\U00002300-\U000023ff"  # 时钟/沙漏/媒体控制
    "\U0000203c\U00002049"  # U+203C / U+2049
    "\U00003030\U0000303d\U00003297\U00003299"  # U+3030 / U+303D / U+3297 / U+3299
    "\ufe0f\u200d\u20e3"  # 变体选择符 / 零宽连接符 / 按键帽
    "]"
)

#: 零宽字符与 BOM（正文里只会干扰排版）。
RE_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2028\u2029\ufeff]")

#: B站内置表情（``rich_text_nodes`` 里 type 为 EMOJI 的节点）在扁平文本中表现为
#: ``[tv_微笑]`` / ``[2026嘉然的画册_糖糖回家]`` 这类中括号 token。
#: 判定规则：``tv_`` 前缀、含下划线的多段名、或命中常见表情名白名单；
#: ``[公告]``、``[A-SOUL]``、``[1_2]`` 这类正常文本保留。
RE_BRACKET_TOKEN = re.compile(r"\[([^\[\]\n]{1,60})\]")

#: 常见 B站表情名（中括号包裹时的完整 token，如 ``[大笑]``、``[doge]``）。
BILI_EMOJI_TOKEN_NAMES: frozenset[str] = frozenset(
    {
        "大笑",
        "微笑",
        "偷笑",
        "呲牙",
        "呆",
        "额",
        "发怒",
        "害羞",
        "汗",
        "机智",
        "加油",
        "惊讶",
        "抠鼻",
        "酷",
        "冷",
        "流汗",
        "么么哒",
        "难过",
        "呕吐",
        "亲亲",
        "求关注",
        "拳头",
        "笑哭",
        "吐",
        "喜欢",
        "眼冒爱心",
        "疑问",
        "晕",
        "抓狂",
        "再见",
        "震惊",
        "委屈",
        "傲娇",
        "辣眼睛",
        "藏狐",
        "打脸",
        "尴尬",
        "怪我咯",
        "滑稽",
        "灵魂出窍",
        "凉了",
        "无语",
        "嫌弃",
        "思考",
        "生病",
        "嘘",
        "点赞",
        "鼓掌",
        "大哭",
        "吃瓜",
        "星星眼",
        "惊喜",
        "妙啊",
        "比心",
        "握手",
        "玫瑰",
        "爱心",
        "蜡烛",
        "口罩",
        "保佑",
        "烈火",
        "炸弹",
        "黑洞",
        "doge",
        "tv_doge",
    },
)
#: 匹配任意中括号 token（用于再按白名单判断是否为表情）。
RE_BRACKET_TOKEN = re.compile(r"\[([^\[\]\n]{1,60})\]")

# ───────────────────────── 群屏蔽与群订阅映射（设置面板配置） ─────────────────────────
#: 群号补全为完整会话 UMO 时使用的平台前缀（本插件仅支持 aiocqhttp）。
DEFAULT_SESSION_PLATFORM = "aiocqhttp"
GROUP_SESSION_PREFIX = f"{DEFAULT_SESSION_PLATFORM}:GroupMessage:"
#: 屏蔽列表 / 映射表里的通配符（匹配全部会话）。
SESSION_WILDCARD = "*"
#: 群订阅映射表「自动同步区」的表头：其后的行由插件维护（可在面板直接编辑）。
GROUP_MAP_AUTO_HEADER = "# ===== 以下为插件自动同步区（群内 /bili_sub 建立的映射，可直接编辑）====="
#: 识别自动同步区表头（兼容手动改写过表头的情况）。
RE_GROUP_MAP_AUTO_HEADER = re.compile(r"(?mi)^[ \t]*#[^\n]*(?:自动同步|auto[-_ ]?sync)[^\n]*$")
#: 映射表文本长度上限与条目上限（防止超长配置拖慢解析）。
MAX_GROUP_MAP_LEN = 20000
MAX_GROUP_MAP_ENTRIES = 200
#: 屏蔽列表条目上限。
MAX_BLOCKED_GROUPS = 200
#: 映射行分隔符：``=`` / ``=>`` / ``->`` / ``→``；也支持直接以空格分隔。
RE_GROUP_MAP_SEP = re.compile(r"\s*(?:=>|->|\u2192|=)\s*")
#: ``UID(过滤器...)`` 形式的映射项（括号取最外层，贪婪到行尾）。
RE_GROUP_MAP_UID_FILTERS = re.compile(r"^(\d{1,20})\s*[(（\[]\s*(.*?)\s*[)）\]]$", re.DOTALL)
#: 映射项里过滤器的分词分隔符（反引号包裹的过滤器内不切分）。
GROUP_MAP_FILTER_SEP = ",;；、，"
#: ``UID(过滤器...)`` 中过滤器之间的分隔符（与指令一致：空格/逗号）。
RE_GROUP_MAP_FILTER_SPLIT = re.compile(r"[\s,，、;；]+")

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
