"""纯工具函数：链接提取、格式化、动态数据解析、API 数据规范化。

本模块只依赖标准库与 :mod:`bili_constants`，便于单元测试。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

try:  # pragma: no cover - 包内导入 / 单文件加载两种场景
    from .bili_constants import (
        BILI_COOKIE_HOST_SUFFIXES,
        BILI_EMOJI_TOKEN_NAMES,
        DEFAULT_DYNAMIC_TYPE_LABEL,
        DYNAMIC_IMAGE_LIMIT,
        DYNAMIC_TOP_TAG,
        DYNAMIC_TYPE_LABELS,
        FAST_KEYWORDS,
        LINK_KEY_PATTERNS,
        MAX_FILTER_REGEX_COUNT,
        MAX_FILTER_REGEX_LEN,
        MAX_UID,
        RE_AV,
        RE_BRACKET_TOKEN,
        RE_BV,
        RE_EMOJI,
        RE_LOTTERY,
        RE_SHORT_LINK,
        RE_VIDEO_LINK,
        RE_ZERO_WIDTH,
        SAFE_FILENAME_RE,
        SENSITIVE_COOKIE_KEYS,
    )
except ImportError:  # pragma: no cover
    from bili_constants import (
        BILI_COOKIE_HOST_SUFFIXES,
        BILI_EMOJI_TOKEN_NAMES,
        DEFAULT_DYNAMIC_TYPE_LABEL,
        DYNAMIC_IMAGE_LIMIT,
        DYNAMIC_TOP_TAG,
        DYNAMIC_TYPE_LABELS,
        FAST_KEYWORDS,
        LINK_KEY_PATTERNS,
        MAX_FILTER_REGEX_COUNT,
        MAX_FILTER_REGEX_LEN,
        MAX_UID,
        RE_AV,
        RE_BRACKET_TOKEN,
        RE_BV,
        RE_EMOJI,
        RE_LOTTERY,
        RE_SHORT_LINK,
        RE_VIDEO_LINK,
        RE_ZERO_WIDTH,
        SAFE_FILENAME_RE,
        SENSITIVE_COOKIE_KEYS,
    )

_HTTP_PREFIXES = ("http://", "https://")
_EMPTY_COUNT_TOKENS = {"", "-", "--", "—", "——", "null", "none", "nan"}
_COUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([万亿]?)")
#: 允许在标记之间断行的空白（换行时丢弃）。
_SPACE_RE = re.compile(r"[ \t]+")
#: 删除 emoji/表情后残留在全角标点旁的空白（如「， 与」「与 （」）。
_SPACE_BEFORE_CJK_PUNCT = re.compile(r"[ \t]+(?=[，。！？；：、）】》」』…（【《「『])")
_SPACE_AFTER_CJK_OPEN = re.compile(r"(?<=[（【《「『])[ \t]+")
#: 全角标点后紧跟中文时的多余空格（英文句子里的「， see」不受影响）。
_SPACE_AFTER_CJK_PUNCT = re.compile(r"(?<=[，。！？；：、…])[ \t]+(?=[\u4e00-\u9fff])")
#: 行头禁则：这些标点不允许出现在换行后的行首（会连同前一个字符一起下移）。
_NO_LINE_START = "，。！？；：、）】》」』…·"


# ───────────────────────── 安全嵌套取值 ─────────────────────────


def dig(obj: Any, *keys: str, default: Any = None) -> Any:
    """沿 *keys* 逐层深入 dict，任一层非 dict / key 缺失即返回 *default*。"""
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k)
    return obj if obj is not None else default


def first_http_url(obj: Any, *keys: str) -> str:
    """从 dict 中按 *keys* 顺序尝试取出首个 http(s) URL。"""
    if not isinstance(obj, dict):
        return ""
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith(_HTTP_PREFIXES):
            return v
        if isinstance(v, dict):
            nested = v.get("src") or v.get("url")
            if isinstance(nested, str) and nested.startswith(_HTTP_PREFIXES):
                return nested
    return ""


def is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_HTTP_PREFIXES)


def link_key(url: Any) -> str:
    """链接的内容指纹：指向同一内容的不同链接返回同一个 key。

    这样 ``t.bilibili.com/<动态ID>`` 与 ``www.bilibili.com/opus/<动态ID>``、
    带 ``?spm_id_from=...`` 查询参数的视频链接、合集 ``sid`` 等都不会被当成两条链接。
    识别不出内容 ID 时退化为「协议 + 域名 + 路径」（忽略查询串与末尾斜杠）。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    for pattern, prefix in LINK_KEY_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return f"{prefix}:{match.group(1).lower()}"
    parsed = urlparse(text)
    if not parsed.netloc:
        return text
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


# ───────────────────────── 链接与 ID 提取 ─────────────────────────


def extract_links_and_ids(text: str) -> list[str]:
    """从文本中提取 B站视频链接 / 短链 / BV / av 号，去重保序。"""
    if not text:
        return []
    low = text.lower()
    if not any(kw in low for kw in FAST_KEYWORDS):
        return []
    hits: list[str] = []
    for regex in (RE_VIDEO_LINK, RE_SHORT_LINK, RE_BV, RE_AV):
        hits.extend(regex.findall(text))
    return list(dict.fromkeys(hits))


def parse_page_index(url_or_text: str) -> int:
    """从 URL 中提取分P索引（0-based）。"""
    try:
        for seg in (urlparse(url_or_text).query or "").split("&"):
            if seg.startswith("p="):
                return max(int(seg.split("=", 1)[1]) - 1, 0)
    except (TypeError, ValueError, IndexError):
        pass
    return 0


def resolve_video_page(
    info: dict[str, Any],
    ref: dict[str, Any],
) -> tuple[dict[str, Any], int, int]:
    """从视频信息 + 引用中解析分P → (page_dict, page_index_0based, page_count)。"""
    pages = info.get("pages") if isinstance(info, dict) else None
    if not isinstance(pages, list):
        pages = []
    page_count = max(len(pages), 1)
    try:
        requested = int(ref.get("page", 0) or 0)
    except (TypeError, ValueError):
        requested = 0
    page_idx = min(max(requested, 0), page_count - 1)
    page = pages[page_idx] if pages and page_idx < len(pages) else {}
    if not isinstance(page, dict):
        page = {}
    return page, page_idx, page_count


# ───────────────────────── 数值 / 格式化 ─────────────────────────


def parse_count(value: Any, default: int = 0) -> int:
    """把 B站返回的计数（``"2.5万"`` / ``"1.1亿"`` / ``123``）解析为整数。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    text = str(value or "").strip().replace(",", "").replace("+", "")
    if text.lower() in _EMPTY_COUNT_TOKENS:
        return default
    match = _COUNT_RE.fullmatch(text)
    if not match:
        return default
    number = float(match.group(1))
    unit = match.group(2)
    multiplier = 100_000_000 if unit == "亿" else 10_000 if unit == "万" else 1
    return round(number * multiplier)


def format_count(value: Any) -> str:
    """把计数格式化为 ``万/亿`` 可读文本。"""
    number = value if isinstance(value, int) and not isinstance(value, bool) else parse_count(value)
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    return str(number)


def format_duration(seconds: Any) -> str:
    """秒数 → ``H:MM:SS`` / ``M:SS``；已是 ``MM:SS`` 形式的字符串原样返回。"""
    if isinstance(seconds, str):
        text = seconds.strip()
        if not text:
            return "0:00"
        if ":" in text:
            return text
        seconds = parse_count(text)
    try:
        total = max(int(seconds or 0), 0)
    except (TypeError, ValueError):
        total = 0
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{sec:02d}" if hours else f"{minutes}:{sec:02d}"


def truncate(s: str, max_len: int, suffix: str = "...") -> str:
    """截断字符串到 *max_len*，超出部分用 *suffix* 替代。"""
    if max_len <= 0:
        return ""
    return s if len(s) <= max_len else s[:max_len] + suffix


# ───────────────────────── 文本清洗与排版 ─────────────────────────


def strip_emoji(text: str) -> str:
    """删除 Unicode emoji（含变体选择符、零宽连接符、按键帽），其余字符原样保留。"""
    if not text:
        return text
    return RE_EMOJI.sub("", text)


def strip_bili_emoji_tokens(text: str) -> str:
    """删除B站内置表情在扁平文本中的 token（如 ``[tv_微笑]``、``[2026画册_糖糖回家]``）。

    只删除「看起来是表情」的中括号 token：``tv_`` 前缀、含下划线的多段名、
    或名称命中常见表情白名单；``[公告]``、``[A-SOUL]``、``[1_2]``、``【重点】`` 保留。
    """
    if not text or "[" not in text:
        return text

    def _drop(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.lower().startswith("tv_") or inner in BILI_EMOJI_TOKEN_NAMES:
            return ""
        if "_" in inner:
            head, _, tail = inner.partition("_")
            # 形如「名称_名称」的表情名；纯数字下划线文本（如 [1_2]）保留
            if head.strip() and tail.strip() and not head.strip().isdigit():
                return ""
        return match.group(0)

    return RE_BRACKET_TOKEN.sub(_drop, text)


def clean_text(text: str, *, keep_newlines: bool = True) -> str:
    """统一清洗B站来源文本：去 emoji / 表情 token / 零宽字符，并规整空白。"""
    if not text:
        return ""
    cleaned = strip_bili_emoji_tokens(strip_emoji(str(text)))
    cleaned = RE_ZERO_WIDTH.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _SPACE_BEFORE_CJK_PUNCT.sub("", cleaned)
    cleaned = _SPACE_AFTER_CJK_OPEN.sub("", cleaned)
    cleaned = _SPACE_AFTER_CJK_PUNCT.sub("", cleaned)
    lines: list[str] = []
    for raw_line in cleaned.split("\n"):
        line = _SPACE_RE.sub(" ", raw_line).strip()
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return ""
    if not keep_newlines:
        return " ".join(part for part in lines if part)
    return "\n".join(lines)


def display_width(text: str) -> int:
    """字符串的显示列宽：东亚宽/全角字符计 2 列，组合记号计 0 列，其余计 1 列。"""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _split_atoms(line: str) -> list[tuple[str, bool]]:
    """把一行拆成「可断行原子」``(文本, 前面是否有空格)``。

    URL / ``@用户`` / ``#话题#`` 保持完整；中文按字断行，ASCII 单词整体保留。
    """
    atoms: list[tuple[str, bool]] = []
    for index, chunk in enumerate(_SPACE_RE.split(line.strip())):
        spaced = index > 0
        if is_http_url(chunk) or chunk.startswith(("@", "#")):
            atoms.append((chunk, spaced))
            continue
        current = ""
        first = True
        for char in chunk:
            if unicodedata.east_asian_width(char) in ("W", "F") or not char.isascii():
                if current:
                    atoms.append((current, spaced and first))
                    current = ""
                    first = False
                atoms.append((char, spaced and first))
                first = False
            else:
                current += char
        if current:
            atoms.append((current, spaced and first))
    return atoms


def _join_atoms(items: Sequence[tuple[str, bool]]) -> str:
    out = ""
    for text, spaced in items:
        if spaced and out:
            out += " "
        out += text
    return out


def wrap_text(text: str, width: int = 0, *, indent: str = "") -> str:
    """按显示列宽自动换行。

    * ``width <= 0`` 时原样返回（用户可关闭自动换行）；
    * 中文逐字断行，英文按单词断行且保留原有空格，URL / ``@用户`` / ``#话题#`` 不被拆开；
    * 已有换行（段落）保留，段落内的续行按 *indent* 缩进；
    * 做基本的「行头禁则」：``，。！？`` 等标点不会被推到下一行行首。
    """
    if width <= 0 or not text:
        return text

    indent_width = display_width(indent)
    out: list[str] = []
    for raw_line in text.split("\n"):
        if display_width(raw_line) <= width:
            out.append(raw_line)
            continue
        prefix = ""
        limit = width
        current: list[tuple[str, bool]] = []
        current_width = 0
        for atom, spaced in _split_atoms(raw_line):
            atom_width = display_width(atom)
            gap = 1 if (spaced and current) else 0
            if current and current_width + gap + atom_width > limit:
                moved = current.pop() if len(current) > 1 and atom[:1] in _NO_LINE_START else None
                out.append(prefix + _join_atoms(current))
                prefix = indent
                limit = width - indent_width
                current = [(moved[0], False)] if moved else []
                current.append((atom, spaced and moved is None))
                current_width = sum(display_width(t) + (1 if s else 0) for t, s in current)
                continue
            current.append((atom, bool(current) and spaced))
            current_width += gap + atom_width
        out.append(prefix + _join_atoms(current))
    return "\n".join(out)


def clean_and_wrap(text: str, width: int = 0, *, indent: str = "") -> str:
    """清洗（去 emoji / 规整空白）后自动换行。"""
    return wrap_text(clean_text(text), width, indent=indent)


def text_value(value: Any) -> str:
    """把接口字段安全地转成可读文本。

    B站接口同一字段在不同动态类型下可能是 ``str`` / ``{"text": ...}`` /
    列表（如 ``rich_text_nodes``），直接 ``str()`` 会把字典原样发到群里，
    因此这里统一取文本并对结果做清洗。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        for key in ("text", "content", "title", "desc", "summary", "name"):
            inner = value.get(key)
            if isinstance(inner, (str, dict, list)):
                resolved = text_value(inner)
                if resolved:
                    return resolved
        return ""
    if isinstance(value, list):
        parts = [text_value(item) for item in value]
        return clean_text("\n".join(part for part in parts if part))
    return clean_text(str(value))


# ───────────────────────── 动态数据提取 ─────────────────────────


def dynamic_id(item: Any) -> str:
    """动态 ID（``id_str``）。"""
    if not isinstance(item, dict):
        return ""
    return str(item.get("id_str") or "").strip()


def modules_of(item: Any) -> dict[str, Any]:
    """取出动态的 modules 字典（兼容 list 与 dict 两种结构）。"""
    raw = item.get("modules") if isinstance(item, dict) else None
    if isinstance(raw, dict):
        return raw
    merged: dict[str, Any] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            for key, value in entry.items():
                if key == "module_type" or not isinstance(value, dict):
                    continue
                merged.setdefault(key, value)
    return merged


def is_top_dynamic(item: Any) -> bool:
    """是否为置顶（置顶动态不参与新动态判定，否则会导致漏推最新动态）。

    新版桌面端接口把置顶标记放在 ``module_author.is_top``，
    旧接口则放在 ``module_tag.text == "置顶"``，这里同时兼容。
    """
    if not isinstance(item, dict):
        return False
    author = dig(item, "modules", "module_author", default={})
    if isinstance(author, dict) and author.get("is_top"):
        return True
    tag = dig(item, "modules", "module_tag", "text")
    if isinstance(tag, str) and tag.strip() == DYNAMIC_TOP_TAG:
        return True
    basic = item.get("basic")
    return isinstance(basic, dict) and bool(basic.get("is_top"))


def dynamic_author_name(item: Any) -> str:
    """动态作者名（新版接口的 ``user.name`` 会被规范化到顶层）。"""
    author = dig(item, "modules", "module_author", default={})
    if not isinstance(author, dict):
        return "UP主"
    name = author.get("name")
    if not name:
        mid = author.get("mid")
        name = f"UID {mid}" if mid else "UP主"
    # UP主昵称本身也可能带 emoji，推送前统一清洗
    return clean_text(str(name)) or "UP主"


def dynamic_author_uid(item: Any) -> str:
    author = dig(item, "modules", "module_author", default={})
    if isinstance(author, dict):
        value = author.get("mid")
        if value:
            return str(value)
    return ""


def dynamic_pub_ts(item: Any) -> int:
    """动态发布时间戳（取不到时返回 0）。"""
    author = dig(item, "modules", "module_author", default={})
    if isinstance(author, dict):
        return parse_count(author.get("pub_ts"), 0)
    return 0


def dynamic_type(item: Any) -> str:
    """动态类型字符串（``DYNAMIC_TYPE_*``；非 dict 输入安全返回空串）。"""
    if not isinstance(item, dict):
        return ""
    return str(item.get("type") or "").strip()


def dynamic_type_label(item: Any) -> str:
    return DYNAMIC_TYPE_LABELS.get(dynamic_type(item), DEFAULT_DYNAMIC_TYPE_LABEL)


def dynamic_major(item: Any) -> dict[str, Any]:
    major = dig(item, "modules", "module_dynamic", "major", default={})
    return major if isinstance(major, dict) else {}


def dynamic_archive(item: Any) -> dict[str, Any]:
    archive = dynamic_major(item).get("archive")
    return archive if isinstance(archive, dict) else {}


def dynamic_opus(item: Any) -> dict[str, Any]:
    opus = dynamic_major(item).get("opus")
    return opus if isinstance(opus, dict) else {}


def dynamic_forward_orig(item: Any) -> dict[str, Any]:
    orig = item.get("orig") if isinstance(item, dict) else None
    return orig if isinstance(orig, dict) else {}


def _rich_text_image_urls(desc: Any) -> list[str]:
    """从 ``rich_text_nodes`` 中提取内嵌图片（仅 IMAGE 节点，不含表情）。"""
    nodes = desc.get("rich_text_nodes") if isinstance(desc, dict) else None
    if not isinstance(nodes, list):
        return []
    urls: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if "IMAGE" not in str(node.get("type") or "").upper():
            continue
        url = first_http_url(node, "image_src", "src", "url", "image")
        if url and url not in urls:
            urls.append(url)
    return urls


def extract_dynamic_images(item: dict[str, Any], *, limit: int = DYNAMIC_IMAGE_LIMIT) -> list[str]:
    """提取动态中的所有图片 URL（视频封面 / 图文多图），最多 *limit* 张。"""
    if limit <= 0:
        return []
    urls: list[str] = []
    major = dynamic_major(item)
    cover = first_http_url(major.get("archive") or {}, "cover")
    if cover:
        urls.append(cover)
    pics = dynamic_opus(item).get("pics")
    if isinstance(pics, list):
        for pic in pics:
            if len(urls) >= limit:
                break
            url = pic if is_http_url(pic) else first_http_url(pic, "url", "img_src", "src")
            if url and url not in urls:
                urls.append(url)
    if len(urls) < limit:
        desc = dig(item, "modules", "module_dynamic", "desc", default={})
        for url in _rich_text_image_urls(desc):
            if len(urls) >= limit:
                break
            if url not in urls:
                urls.append(url)
    if not urls:
        orig = dynamic_forward_orig(item)
        if orig:
            return extract_dynamic_images(orig, limit=limit)
    return urls[:limit]


def extract_dynamic_cover(item: dict[str, Any]) -> str:
    """提取动态封面图 URL（视频封面 > 图文首图 > 转发原动态）。"""
    images = extract_dynamic_images(item, limit=1)
    return images[0] if images else ""


def extract_dynamic_text(item: dict[str, Any]) -> str:
    """提取动态可读文本（描述 + opus 摘要 + 视频标题/简介），去重保序并清洗 emoji。"""
    dynamic = dig(item, "modules", "module_dynamic", default={})
    if not isinstance(dynamic, dict):
        return ""
    parts: list[str] = []
    text = dig(dynamic, "desc", "text")
    if text:
        parts.append(text_value(text))
    summary = dig(dynamic, "major", "opus", "summary", "text")
    if summary:
        parts.append(text_value(summary))
    archive = dynamic_archive(item)
    for key in ("title", "desc"):
        value = archive.get(key)
        if value:
            parts.append(text_value(value))
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            unique.append(part)
    return "\n".join(unique).strip()


def extract_forward_text(item: dict[str, Any]) -> str:
    """转发动态的原文摘要（``@原作者: 正文``），没有原文时返回空串。"""
    orig = dynamic_forward_orig(item)
    if not orig:
        return ""
    text = extract_dynamic_text(orig)
    if not text:
        return ""
    return f"@{dynamic_author_name(orig)}: {text}"


#: ``major`` 中的卡片类型 → (类型名, URL 字段候选)。
_CARD_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "archive": ("视频", ("jump_url", "short_link_v2")),
    "article": ("专栏", ("jump_url",)),
    "opus": ("图文", ()),
    "live_rcmd": ("直播", ("jump_url",)),
    "live_play_info": ("直播", ("link", "jump_url")),
    "common": ("卡片", ("jump_url",)),
    "common_square": ("卡片", ("jump_url",)),
    "medialist": ("合集", ("jump_url",)),
    "music": ("音乐", ("jump_url",)),
    "pgc": ("番剧", ("jump_url",)),
    "courses": ("课程", ("jump_url",)),
    "upower_common_square": ("充电", ("jump_url",)),
    "live": ("直播", ("jump_url",)),
}


def _card_number(*values: Any) -> str:
    for value in values:
        if value not in (None, "", "-"):
            return format_count(value)
    return ""


def dynamic_card(item: dict[str, Any]) -> dict[str, str]:
    """按 ``major`` 类型提取卡片信息，供各类型专属排版使用。

    返回键固定为 ``kind`` / ``title`` / ``desc`` / ``stat`` / ``url`` / ``room``，
    取不到的字段为空串，调用方无需做分支判断。
    """
    major = dynamic_major(item)
    card = {"kind": "", "title": "", "desc": "", "stat": "", "url": "", "room": ""}
    if not major:
        return card

    for key, (kind, url_keys) in _CARD_KINDS.items():
        data = major.get(key)
        if not isinstance(data, dict):
            continue
        card["kind"] = kind
        if key in ("live_rcmd", "live_play_info", "live"):
            data = _live_payload(data)
        elif key == "common":
            # 通用卡片/互动抽奖把真正的内容放在 square / lottery 子对象里
            for field in ("square", "lottery"):
                inner = data.get(field)
                if isinstance(inner, dict):
                    data = {**data, **inner}
                    break
            if dynamic_type(item) == "DYNAMIC_TYPE_LOTTERY":
                card["kind"] = "抽奖"
        card["title"] = text_value(data.get("title") or data.get("name"))
        card["desc"] = text_value(data.get("desc") or data.get("summary"))
        raw_stat = data.get("stat")
        stat: dict[str, Any] = raw_stat if isinstance(raw_stat, dict) else {}
        if key == "archive":
            card["stat"] = _archive_stat_line(data, stat)
        elif key == "article":
            raw_stats = data.get("stats")
            views = _card_number(
                (raw_stats if isinstance(raw_stats, dict) else {}).get("view"),
                data.get("view"),
            )
            if views:
                card["stat"] = f"阅读 {views}"
        elif key in ("live_rcmd", "live_play_info", "live"):
            card["stat"] = _live_stat_line(data)
            card["room"] = str(data.get("room_id") or data.get("roomId") or "").strip()
        elif key == "medialist":
            count = _card_number(stat.get("count"), data.get("count"))
            if count:
                card["stat"] = f"内容 {count}"
        for field in url_keys:
            url = data.get(field)
            if is_http_url(url):
                card["url"] = str(url)
                break
        if not card["url"]:
            card["url"] = first_http_url(data, "jump_url", "link", "url", "short_link_v2")
        break
    return card


def _archive_stat_line(archive: dict[str, Any], stat: dict[str, Any]) -> str:
    """视频卡片的统计行（播放 / 弹幕 / 时长），字段缺失则省略。"""
    parts: list[str] = []
    if stat.get("play") is not None:
        parts.append(f"播放 {format_count(stat.get('play'))}")
    if stat.get("danmaku") is not None:
        parts.append(f"弹幕 {format_count(stat.get('danmaku'))}")
    duration = archive.get("duration_text") or archive.get("duration")
    if duration:
        parts.append(f"时长 {format_duration(duration)}")
    return " | ".join(parts)


def _live_payload(data: dict[str, Any]) -> dict[str, Any]:
    """展开直播卡片数据：``live_rcmd.content`` 是 JSON 字符串，``live_play_info`` 是嵌套字典。"""
    merged = data
    content = data.get("content")
    if isinstance(content, str) and content.strip().startswith("{"):
        with suppress(ValueError, TypeError):
            # 非 dict 的 JSON（理论不会出现）会在解包时抛 TypeError，被 suppress 吞掉
            merged = {**merged, **json.loads(content)}
    play = merged.get("live_play_info")
    if isinstance(play, dict):
        merged = {**merged, **play}
    return merged


def _live_stat_line(data: dict[str, Any]) -> str:
    """直播卡片的统计行（人气 / 看过人数）。"""
    parts: list[str] = []
    popularity = _card_number(data.get("popularity"), data.get("online"))
    if popularity:
        parts.append(f"人气 {popularity}")
    watched = _card_number(data.get("watched_show"))
    if watched:
        parts.append(f"看过 {watched}")
    return " | ".join(parts)


def extract_live_title(live_info: dict[str, Any]) -> str:
    """直播标题（不同接口字段不一致，取清洗后的首个非空值）。"""
    for key in ("title", "live_title", "room_name"):
        cleaned = text_value(live_info.get(key))
        if cleaned:
            return cleaned
    return ""


def extract_live_cover(live_info: dict[str, Any]) -> str:
    """提取直播封面（不同接口字段命名不一致，按优先级尝试）。"""
    return first_http_url(
        live_info,
        "cover_from_user",
        "user_cover",
        "cover",
        "keyframe",
    )


def is_lottery_text(text: str) -> bool:
    if not text:
        return False
    return "互动抽奖" in text or bool(RE_LOTTERY.search(text))


def is_forward_lottery(item: dict[str, Any]) -> bool:
    """转发的原动态是否为互动抽奖。"""
    nodes = dig(
        item,
        "orig",
        "modules",
        "module_dynamic",
        "major",
        "opus",
        "summary",
        "rich_text_nodes",
        default=[],
    )
    if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict) and nodes[0].get("text") == "互动抽奖":
        return True
    # 新版接口：抽奖动态常在 module_dynamic.type == "DYNAMIC_TYPE_LOTTERY"
    orig_type = dig(item, "orig", "modules", "module_dynamic", "type")
    return str(orig_type or "").upper() == "DYNAMIC_TYPE_LOTTERY"


# ───────────────────────── 过滤参数解析 ─────────────────────────


def parse_filter_args(
    args: list[str],
    valid_types: frozenset[str],
) -> tuple[list[str], list[str]]:
    """将参数列表拆分为 类型过滤 + 正则过滤，各自去重保序。"""
    types: list[str] = []
    regex: list[str] = []
    for a in args:
        t = str(a).strip()
        if not t:
            continue
        (types if t in valid_types else regex).append(t)
    return list(dict.fromkeys(types)), list(dict.fromkeys(regex))


# ───────────────────────── API modules 规范化 ─────────────────────────


def normalize_modules(item: dict[str, Any]) -> None:
    """将 ``desktop/v1/feed/space`` 返回的新格式规范化为旧格式。

    新 API  ``modules`` 是 list（每元素 ``{"module_type": "…", "module_xxx": {…}}``），
    旧代码 ``modules`` 是 dict：``{"module_author": {…}, "module_dynamic": {…}, …}``。
    同时做字段层级映射：

    * ``author.user.{name,mid,face}`` → ``author.{name,mid,face}``
    * ``module_desc`` → ``module_dynamic.desc``（即使没有 ``module_dynamic`` 也会补建，
      否则纯文字/Opus 动态的正文会整体丢失）
    * ``dyn_draw.items[].src`` → ``major.opus.pics[].url``
    * ``dyn_archive`` → ``major.archive``
    * ``dyn_forward.item`` → ``item["orig"]``（递归）
    """
    if not isinstance(item, dict):
        return
    raw = item.get("modules")
    merged: dict[str, Any]
    if isinstance(raw, dict):
        merged = raw
    elif isinstance(raw, list):
        merged = {}
        for entry in raw:
            if isinstance(entry, dict):
                for k, v in entry.items():
                    if k != "module_type" and isinstance(v, dict):
                        merged[k] = v
        item["modules"] = merged
    else:
        return

    # Step 1: author.user → author 顶层
    author = merged.get("module_author")
    if isinstance(author, dict):
        user = author.get("user")
        if isinstance(user, dict):
            for field in ("name", "mid", "face"):
                if not author.get(field):
                    author[field] = user.get(field)

    desc_mod = merged.get("module_desc")
    dyn_mod = merged.get("module_dynamic")
    if not isinstance(dyn_mod, dict):
        # 新版接口中纯文字 / Opus 动态可能完全没有 module_dynamic
        if isinstance(desc_mod, dict):
            dyn_mod = merged["module_dynamic"] = {}
        else:
            return

    # Step 2: module_desc → module_dynamic.desc
    if isinstance(desc_mod, dict) and "desc" not in dyn_mod:
        dyn_mod["desc"] = desc_mod

    # Step 3: 新字段 → 旧 major 结构
    if "major" not in dyn_mod:
        major: dict[str, Any] = {}
        dyn_draw = dyn_mod.get("dyn_draw")
        if isinstance(dyn_draw, dict):
            pics = [
                {
                    "url": d.get("src"),
                    "width": d.get("width", 0),
                    "height": d.get("height", 0),
                }
                for d in (dyn_draw.get("items") or [])
                if isinstance(d, dict) and d.get("src")
            ]
            if pics:
                major["opus"] = {
                    "pics": pics,
                    "summary": desc_mod if isinstance(desc_mod, dict) else {},
                }
        if "opus" not in major:
            image_urls = _rich_text_image_urls(desc_mod)
            if image_urls:
                major["opus"] = {
                    "pics": [{"url": url} for url in image_urls],
                    "summary": desc_mod if isinstance(desc_mod, dict) else {},
                }
        dyn_archive = dyn_mod.get("dyn_archive")
        if isinstance(dyn_archive, dict):
            major["archive"] = dyn_archive
        if major:
            dyn_mod["major"] = major

    # Step 4: 转发 dyn_forward → item["orig"]（递归）
    dyn_forward = dyn_mod.get("dyn_forward")
    if isinstance(dyn_forward, dict):
        orig = dyn_forward.get("item")
        if isinstance(orig, dict):
            normalize_modules(orig)
            item["orig"] = orig


# ───────────────────────── 安全 / 输入校验 / 资源限制 ─────────────────────────


def mask_secret(value: str | None, keep: int = 4) -> str:
    """把 Cookie / Token 等敏感串脱敏为 ``SESSDATA=abc***`` 形式。

    只保留每段取值的前 *keep* 个字符，避免日志与指令输出泄漏登录态。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    masked_parts: list[str] = []
    for segment in re.split(r"[;\n]+", text):
        chunk = segment.strip()
        if not chunk:
            continue
        key, sep, val = chunk.partition("=")
        val = val.strip()
        if not sep or not val:
            masked_parts.append("***")
            continue
        if len(val) <= keep:
            masked_parts.append(f"{key.strip()}=***")
        else:
            masked_parts.append(f"{key.strip()}={val[:keep]}***")
    return "; ".join(masked_parts)


def has_sensitive_cookie_text(text: str) -> bool:
    """判断文本中是否出现 Cookie 关键字（用于「禁止记录原始 Cookie」的兜底断言）。"""
    lowered = str(text or "")
    return any(key.lower() in lowered.lower() for key in SENSITIVE_COOKIE_KEYS)


def sanitize_cookie(value: str | None) -> str:
    """清理 Cookie 中的换行/控制字符，防止 HTTP 头注入。"""
    text = str(value or "")
    text = text.replace("\r", "").replace("\n", "").replace("\0", "")
    return text.strip()


def host_of(url: str | None) -> str:
    """取 URL 的 host（小写、去除端口）。"""
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except (TypeError, ValueError):
        return ""


def is_trusted_host(url: str | None, suffixes: tuple[str, ...] = BILI_COOKIE_HOST_SUFFIXES) -> bool:
    """判断 URL 主机是否属于受信任域名（按域名边界匹配后缀）。"""
    host = host_of(url)
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def is_valid_uid(value: Any) -> bool:
    """校验 UID：纯数字且处于合理区间。"""
    text = str(value).strip() if value is not None else ""
    if not text.isdigit():
        return False
    uid = int(text)
    return 0 < uid <= MAX_UID


def sanitize_filename(name: str | None, *, fallback: str = "file", max_len: int = 80) -> str:
    """把任意字符串转换为安全的文件名片段（不含路径分隔符与控制字符）。"""
    text = str(name or "").strip()
    text = SAFE_FILENAME_RE.sub("_", text).strip("._-")
    return text[:max_len] or fallback


def format_bytes(size: int | float | None) -> str:
    """人类可读的字节数（用于状态输出与日志）。"""
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return "0B"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"  # pragma: no cover - 仅 inf/nan 等极端值可达


def compile_filter_patterns(patterns: Sequence[Any]) -> list[re.Pattern[str]]:
    """把用户正则编译为 Pattern 列表。

    生产环境约束（防止 ReDoS 与配置爆炸）：

    * 单个模式长度不超过 :data:`MAX_FILTER_REGEX_LEN`；
    * 模式数量不超过 :data:`MAX_FILTER_REGEX_COUNT`（去重后仍受此限制）；
    * 非法正则直接跳过（调用方应通过 :func:`validate_filter_patterns` 提前提示）。
    """
    compiled: list[re.Pattern[str]] = []
    seen: set[str] = set()
    for raw in patterns or []:
        if isinstance(raw, re.Pattern):
            pattern = raw.pattern
            if pattern in seen:
                continue
            seen.add(pattern)
            compiled.append(raw)
        else:
            pattern = str(raw or "").strip()
            if not pattern or pattern in seen or len(pattern) > MAX_FILTER_REGEX_LEN:
                continue
            seen.add(pattern)
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
        if len(compiled) >= MAX_FILTER_REGEX_COUNT:
            break
    return compiled


def validate_filter_patterns(patterns: Sequence[Any]) -> str:
    """返回第一个非法正则的说明，全部合法时返回空串。"""
    seen: set[str] = set()
    valid_count = 0
    for raw in patterns or []:
        pattern = raw.pattern if isinstance(raw, re.Pattern) else str(raw or "").strip()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        if len(pattern) > MAX_FILTER_REGEX_LEN:
            return f"正则过长（上限 {MAX_FILTER_REGEX_LEN} 字符）：{pattern[:32]}..."
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"正则非法（{exc}）：{pattern[:32]}"
        valid_count += 1
        if valid_count > MAX_FILTER_REGEX_COUNT:
            return f"正则数量过多（上限 {MAX_FILTER_REGEX_COUNT} 条）"
    return ""


def matches_any_pattern(text: str, patterns: Sequence[re.Pattern[str] | str]) -> bool:
    """文本是否命中任一正则（已编译 Pattern 或原始字符串均可）。

    所有过滤正则按 **大小写不敏感** 语义匹配（与 :func:`compile_filter_patterns` 一致），
    单条匹配异常不影响整体判断。
    """
    if not text or not patterns:
        return False
    for pattern in patterns:
        try:
            if isinstance(pattern, re.Pattern):
                if pattern.search(text):
                    return True
            elif pattern and re.search(str(pattern), text, re.IGNORECASE):
                return True
        except (re.error, TypeError, MemoryError):  # pragma: no cover - 极端兜底
            continue
    return False
