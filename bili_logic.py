"""业务逻辑：消息链构建、动态过滤、订阅管理辅助。"""
from __future__ import annotations

import re
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger

try:
    from .bili_helpers import (
        dig, extract_dynamic_images, extract_dynamic_text,
        format_count, format_duration, is_forward_lottery, is_lottery_text,
        resolve_video_page, truncate,
    )
    from .bili_models import Subscription
except ImportError:
    from bili_helpers import (
        dig, extract_dynamic_images, extract_dynamic_text,
        format_count, format_duration, is_forward_lottery, is_lottery_text,
        resolve_video_page, truncate,
    )
    from bili_models import Subscription


# ───────────────────────── 视频消息链 ─────────────────────────

def build_video_chain(info: dict[str, Any], ref: dict[str, Any]) -> list[object]:
    """根据视频信息和引用构建消息链（封面 + 文本）。"""
    stat = info.get("stat") or {}
    owner = info.get("owner") or {}
    page, page_idx, page_count = resolve_video_page(info, ref)

    bvid = info.get("bvid", "")
    duration = int(page.get("duration") or info.get("duration") or 0)
    cover = str(info.get("pic") or "").strip()

    chain: list[object] = []
    if cover.startswith(("http://", "https://")):
        try:
            chain.append(Comp.Image.fromURL(cover))
        except (TypeError, ValueError):
            pass

    lines = [
        f"{info.get('title', '未知标题')}",
        f"UP: {owner.get('name', '未知UP')}",
        f"播放: {format_count(int(stat.get('view', 0) or 0))}",
        f"点赞: {format_count(int(stat.get('like', 0) or 0))}",
        f"时长: {format_duration(duration)}",
    ]
    if page_count > 1:
        lines.append(f"分P: {page_idx + 1}/{page_count} {page.get('part') or ''}".strip())
    desc = (info.get("desc") or "").strip()
    if desc:
        lines.append(f"简介: {truncate(desc, 120)}")
    if bvid:
        lines.append(f"https://www.bilibili.com/video/{bvid}")
    chain.append(Comp.Plain("\n".join(lines)))
    return chain


# ───────────────────────── 动态消息链 ─────────────────────────

_DYN_TYPE_LABEL: dict[str, str] = {
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_WORD": "文字动态",
    "DYNAMIC_TYPE_AV": "视频动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏文章",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播推荐",
}


def _dynamic_author_name(item: dict[str, Any]) -> str:
    author = dig(item, "modules", "module_author", default={})
    if not isinstance(author, dict):
        return "UP主"
    name = author.get("name")
    if not name:
        mid = author.get("mid")
        name = f"UID {mid}" if mid else "UP主"
    return str(name).strip()


def build_dynamic_chain(item: dict[str, Any]) -> list[object]:
    """构建动态推送消息链：图片 + 富文本摘要 + 链接。"""
    chain: list[object] = []
    dyn_type = str(item.get("type") or "")
    dyn_id = item.get("id_str") or ""
    name = _dynamic_author_name(item)
    text = str(dig(item, "modules", "module_dynamic", "desc", "text", default="")).strip()

    # ── 1. 头部：UP主 + 动态类型 ──
    type_label = _DYN_TYPE_LABEL.get(dyn_type, "动态")
    header = f"{name} 发布了{type_label}"
    chain.append(Comp.Plain(header))

    # ── 2. 图片（视频封面 / 图文多图） ──
    images = extract_dynamic_images(item)
    for url in images:
        try:
            chain.append(Comp.Image.fromURL(url))
        except (TypeError, ValueError):
            pass

    # ── 3. 正文摘要 ──
    body_parts: list[str] = []
    if text:
        body_parts.append(truncate(text, 300))
    # 视频动态额外展示标题/播放量
    if dyn_type == "DYNAMIC_TYPE_AV":
        archive = dig(item, "modules", "module_dynamic", "major", "archive", default={})
        if isinstance(archive, dict):
            title = archive.get("title")
            if title:
                body_parts.append(title)
            stat = archive.get("stat") or {}
            if isinstance(stat, dict):
                play = stat.get("play")
                if play is not None:
                    body_parts.append(f"播放: {format_count(int(play))}")
    # 转发动态展示原文摘要
    if dyn_type == "DYNAMIC_TYPE_FORWARD":
        orig = item.get("orig") or {}
        if isinstance(orig, dict):
            orig_name = _dynamic_author_name(orig)
            orig_text = str(dig(orig, "modules", "module_dynamic", "desc", "text", default="")).strip()
            if orig_text:
                body_parts.append(f"@{orig_name}: {truncate(orig_text, 120)}")

    if body_parts:
        chain.append(Comp.Plain("\n".join(body_parts)))

    # ── 4. 链接 ──
    if dyn_id:
        chain.append(Comp.Plain(f"https://t.bilibili.com/{dyn_id}"))

    return chain


# ───────────────────────── 动态过滤 ─────────────────────────

def should_filter_dynamic(item: dict[str, Any], sub: Subscription) -> bool:
    """判断该动态是否应被过滤（True = 过滤掉）。"""
    filters = set(sub.filter_types or [])
    if not filters and not sub.filter_regex:
        return False

    dyn_type = str(item.get("type") or "")
    text = extract_dynamic_text(item)

    if dyn_type == "DYNAMIC_TYPE_FORWARD":
        if "forward" in filters:
            return True
        if "forward_lottery" in filters and is_forward_lottery(item):
            return True
        if "lottery" in filters and is_lottery_text(text):
            return True
    elif dyn_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
        if "draw" in filters:
            return True
        if "lottery" in filters and is_lottery_text(text):
            return True
    elif dyn_type == "DYNAMIC_TYPE_AV" and "video" in filters:
        return True
    elif dyn_type == "DYNAMIC_TYPE_ARTICLE" and "article" in filters:
        return True

    if text and sub.filter_regex:
        for pat in sub.filter_regex:
            try:
                if re.search(pat, text):
                    return True
            except re.error:
                logger.warning("无效正则表达式: %s", pat)
    return False


# ───────────────────────── 订阅辅助 ─────────────────────────

def append_recent_id(sub: Subscription, dyn_id: str, limit: int) -> None:
    if not dyn_id:
        return
    recent = sub.recent_ids
    if dyn_id in recent:
        recent.remove(dyn_id)
    recent.append(dyn_id)
    if len(recent) > limit:
        sub.recent_ids = recent[-limit:]


def build_uid_targets(
    subscriptions: dict[str, list[Subscription]],
) -> dict[int, list[tuple[str, Subscription]]]:
    targets: dict[int, list[tuple[str, Subscription]]] = {}
    for session, subs in subscriptions.items():
        for sub in subs:
            targets.setdefault(sub.uid, []).append((session, sub))
    return targets
