import re
from typing import Any, Dict, List, Tuple

import astrbot.api.message_components as Comp
from astrbot.api import logger

try:
    from .bili_helpers import (
        extract_dynamic_cover,
        extract_dynamic_text,
        format_count,
        format_duration,
        is_forward_lottery,
        is_lottery_text,
    )
    from .bili_models import Subscription
except ImportError:
    from bili_helpers import (
        extract_dynamic_cover,
        extract_dynamic_text,
        format_count,
        format_duration,
        is_forward_lottery,
        is_lottery_text,
    )
    from bili_models import Subscription


def build_video_chain(info: Dict[str, Any], ref: Dict[str, Any]) -> List[object]:
    chain: List[object] = []

    stat = info.get("stat", {})
    owner = info.get("owner", {})
    pages = info.get("pages") or []
    page_count = len(pages) if pages else 1
    page_index = min(int(ref.get("page", 0) or 0), max(page_count - 1, 0))
    page = pages[page_index] if pages and page_index < len(pages) else {}

    bvid = info.get("bvid", "")
    title = info.get("title", "未知标题")
    up_name = owner.get("name", "未知UP")
    desc = (info.get("desc") or "").strip()
    duration = int(page.get("duration") or info.get("duration") or 0)
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
    part = page.get("part") or ""
    cover_url = str(info.get("pic") or "").strip()

    if cover_url.startswith("http://") or cover_url.startswith("https://"):
        try:
            chain.append(Comp.Image.fromURL(cover_url))
        except Exception:
            pass

    lines = [
        f"🎬 {title}",
        f"👤 UP: {up_name}",
        f"▶️ 播放: {format_count(int(stat.get('view', 0) or 0))}",
        f"👍 点赞: {format_count(int(stat.get('like', 0) or 0))}",
        f"⏱️ 时长: {format_duration(duration)}",
    ]
    if page_count > 1:
        lines.append(f"📄 分P: {page_index + 1}/{page_count} {part}".strip())
    if desc:
        lines.append(f"📝 简介: {desc[:120]}{'...' if len(desc) > 120 else ''}")
    if url:
        lines.append(url)
    chain.append(Comp.Plain("\n".join(lines)))
    return chain


def dynamic_summary(item: Dict[str, Any]) -> str:
    modules = item.get("modules", {}) if isinstance(item, dict) else {}
    author = modules.get("module_author", {}) if isinstance(modules, dict) else {}
    dynamic = modules.get("module_dynamic", {}) if isinstance(modules, dict) else {}
    desc = dynamic.get("desc", {}) if isinstance(dynamic, dict) else {}
    text = desc.get("text") or ""
    name = author.get("name") or "未知UP"
    dyn_id = item.get("id_str") or ""
    jump = f"https://t.bilibili.com/{dyn_id}" if dyn_id else ""
    lines = [f"📣 {name} 发布了新动态"]
    if text:
        lines.append(text[:180] + ("..." if len(text) > 180 else ""))
    if jump:
        lines.append(jump)
    return "\n".join(lines)


def build_dynamic_chain(item: Dict[str, Any]) -> List[object]:
    chain: List[object] = []
    cover = extract_dynamic_cover(item)
    if cover:
        try:
            chain.append(Comp.Image.fromURL(cover))
        except Exception:
            pass
    chain.append(Comp.Plain(dynamic_summary(item)))
    return chain


def match_filter_regex(text: str, filter_regex: List[str]) -> bool:
    if not text or not filter_regex:
        return False
    for pattern in filter_regex:
        try:
            if re.search(pattern, text):
                return True
        except re.error:
            logger.warning("无效正则表达式: %s", pattern)
            continue
    return False


def should_filter_dynamic(item: Dict[str, Any], sub: Subscription) -> bool:
    filter_types = set(sub.filter_types or [])
    dynamic_type = str(item.get("type") or "")
    text = extract_dynamic_text(item)

    if dynamic_type == "DYNAMIC_TYPE_FORWARD":
        if "forward" in filter_types:
            return True
        if "forward_lottery" in filter_types and is_forward_lottery(item):
            return True
        if "lottery" in filter_types and is_lottery_text(text):
            return True
    elif dynamic_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
        if "draw" in filter_types:
            return True
        if "lottery" in filter_types and is_lottery_text(text):
            return True
    elif dynamic_type == "DYNAMIC_TYPE_AV":
        if "video" in filter_types:
            return True
    elif dynamic_type == "DYNAMIC_TYPE_ARTICLE":
        if "article" in filter_types:
            return True

    if match_filter_regex(text, sub.filter_regex or []):
        return True
    return False


def append_recent_id(sub: Subscription, dyn_id: str, recent_ids_limit: int) -> None:
    if not dyn_id:
        return
    recent = sub.recent_ids or []
    if dyn_id in recent:
        recent.remove(dyn_id)
    recent.append(dyn_id)
    if len(recent) > recent_ids_limit:
        recent = recent[-recent_ids_limit:]
    sub.recent_ids = recent


def build_uid_targets(subscriptions: Dict[str, List[Subscription]]) -> Dict[int, List[Tuple[str, Subscription]]]:
    uid_targets: Dict[int, List[Tuple[str, Subscription]]] = {}
    for session, items in subscriptions.items():
        for sub in items:
            uid_targets.setdefault(sub.uid, []).append((session, sub))
    return uid_targets
