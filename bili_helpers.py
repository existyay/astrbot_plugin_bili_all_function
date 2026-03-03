import re
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse

try:
    from .bili_constants import (
        BILI_AV_PATTERN,
        BILI_BV_PATTERN,
        BILI_SHORT_LINK_PATTERN,
        BILI_VIDEO_LINK_PATTERN,
    )
except ImportError:
    from bili_constants import (
        BILI_AV_PATTERN,
        BILI_BV_PATTERN,
        BILI_SHORT_LINK_PATTERN,
        BILI_VIDEO_LINK_PATTERN,
    )


def extract_links_and_ids(text: str) -> List[str]:
    if not text:
        return []
    lower_text = text.lower()
    if (
        "bilibili.com/video/" not in lower_text
        and "b23.tv/" not in lower_text
        and "bili2233.cn/" not in lower_text
        and "bv" not in lower_text
        and "av" not in lower_text
    ):
        return []

    links: List[str] = []
    links.extend(re.findall(BILI_VIDEO_LINK_PATTERN, text, re.IGNORECASE))
    links.extend(re.findall(BILI_SHORT_LINK_PATTERN, text, re.IGNORECASE))
    links.extend(re.findall(BILI_BV_PATTERN, text))
    links.extend(re.findall(BILI_AV_PATTERN, text, re.IGNORECASE))
    seen = set()
    unique: List[str] = []
    for item in links:
        lower = item.lower()
        if lower in seen:
            continue
        seen.add(lower)
        unique.append(item)
    return unique


def parse_page_index(url_or_text: str) -> int:
    try:
        parsed = urlparse(url_or_text)
        query = parsed.query or ""
        if "p=" not in query:
            return 0
        segs = [part for part in query.split("&") if part.startswith("p=")]
        if not segs:
            return 0
        return max(int(segs[0].split("=", 1)[1]) - 1, 0)
    except Exception:
        return 0


def format_count(value: int) -> str:
    if value >= 100000000:
        return f"{value / 100000000:.1f}亿"
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return str(value)


def format_duration(seconds: int) -> str:
    m, s = divmod(max(seconds, 0), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def extract_dynamic_cover(item: Dict[str, Any]) -> str:
    modules = item.get("modules") or {}
    if not isinstance(modules, dict):
        return ""
    dynamic = modules.get("module_dynamic") or {}
    if not isinstance(dynamic, dict):
        return ""
    major = dynamic.get("major") or {}
    if not isinstance(major, dict):
        return ""

    archive = major.get("archive") or {}
    if isinstance(archive, dict):
        cover = archive.get("cover")
        if isinstance(cover, str) and cover.startswith(("http://", "https://")):
            return cover

    opus = major.get("opus") or {}
    if isinstance(opus, dict):
        pics = opus.get("pics") or []
        if isinstance(pics, list) and pics:
            first = pics[0]
            if isinstance(first, dict):
                for key in ("url", "img_src", "src"):
                    val = first.get(key)
                    if isinstance(val, str) and val.startswith(("http://", "https://")):
                        return val
            elif isinstance(first, str) and first.startswith(("http://", "https://")):
                return first
    return ""


def is_top_dynamic(item: Dict[str, Any]) -> bool:
    modules = item.get("modules") or {}
    module_tag = modules.get("module_tag") if isinstance(modules, dict) else None
    if not isinstance(module_tag, dict):
        return False
    return str(module_tag.get("text") or "") == "置顶"


def extract_dynamic_text(item: Dict[str, Any]) -> str:
    modules = item.get("modules") or {}
    if not isinstance(modules, dict):
        return ""
    dynamic = modules.get("module_dynamic") or {}
    if not isinstance(dynamic, dict):
        return ""

    parts: List[str] = []
    desc = dynamic.get("desc") or {}
    if isinstance(desc, dict):
        text = desc.get("text")
        if text:
            parts.append(str(text))

    major = dynamic.get("major") or {}
    if isinstance(major, dict):
        opus = major.get("opus") or {}
        if isinstance(opus, dict):
            summary = opus.get("summary") or {}
            if isinstance(summary, dict) and summary.get("text"):
                parts.append(str(summary.get("text")))

        archive = major.get("archive") or {}
        if isinstance(archive, dict):
            if archive.get("title"):
                parts.append(str(archive.get("title")))
            if archive.get("desc"):
                parts.append(str(archive.get("desc")))

    return "\n".join([x for x in parts if x]).strip()


def is_lottery_text(text: str) -> bool:
    if not text:
        return False
    if "互动抽奖" in text:
        return True
    return bool(re.search(r"恭喜.*等\d+位同学中奖", text))


def is_forward_lottery(item: Dict[str, Any]) -> bool:
    try:
        orig = item.get("orig") or {}
        modules = orig.get("modules") or {}
        dynamic = modules.get("module_dynamic") or {}
        major = dynamic.get("major") or {}
        opus = major.get("opus") or {}
        summary = opus.get("summary") or {}
        nodes = summary.get("rich_text_nodes") or []
        if nodes and isinstance(nodes[0], dict):
            return str(nodes[0].get("text") or "") == "互动抽奖"
    except Exception:
        return False
    return False


def parse_filter_args(args: List[str], valid_types: Set[str]) -> Tuple[List[str], List[str]]:
    filter_types: List[str] = []
    filter_regex: List[str] = []
    for arg in args:
        token = str(arg).strip()
        if not token:
            continue
        if token in valid_types:
            filter_types.append(token)
        else:
            filter_regex.append(token)
    filter_types = list(dict.fromkeys(filter_types))
    filter_regex = list(dict.fromkeys(filter_regex))
    return filter_types, filter_regex
