"""业务逻辑：消息链构建、动态过滤、订阅去重与选择。

除 :mod:`astrbot` 的消息组件外均为纯函数，便于完整覆盖测试。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

import astrbot.api.message_components as Comp

try:  # pragma: no cover - 包内导入 / 单文件加载两种场景
    from .bili_constants import (
        DYNAMIC_DETAIL_URL,
        DYNAMIC_TEXT_LIMIT,
        DYNAMIC_TEXT_WRAP_WIDTH,
        LIVE_ROOM_URL,
        VIDEO_DESC_LIMIT,
        VIDEO_DETAIL_URL,
    )
    from .bili_helpers import (
        clean_and_wrap,
        display_width,
        dynamic_archive,
        dynamic_author_name,
        dynamic_card,
        dynamic_forward_orig,
        dynamic_id,
        dynamic_type,
        dynamic_type_label,
        extract_dynamic_images,
        extract_dynamic_text,
        extract_forward_text,
        extract_live_cover,
        extract_live_title,
        format_count,
        format_duration,
        is_forward_lottery,
        is_http_url,
        is_lottery_text,
        is_top_dynamic,
        matches_any_pattern,
        resolve_video_page,
        text_value,
        truncate,
        wrap_text,
    )
    from .bili_models import Subscription
except ImportError:  # pragma: no cover
    from bili_constants import (
        DYNAMIC_DETAIL_URL,
        DYNAMIC_TEXT_LIMIT,
        DYNAMIC_TEXT_WRAP_WIDTH,
        LIVE_ROOM_URL,
        VIDEO_DESC_LIMIT,
        VIDEO_DETAIL_URL,
    )
    from bili_helpers import (
        clean_and_wrap,
        display_width,
        dynamic_archive,
        dynamic_author_name,
        dynamic_card,
        dynamic_forward_orig,
        dynamic_id,
        dynamic_type,
        dynamic_type_label,
        extract_dynamic_images,
        extract_dynamic_text,
        extract_forward_text,
        extract_live_cover,
        extract_live_title,
        format_count,
        format_duration,
        is_forward_lottery,
        is_http_url,
        is_lottery_text,
        is_top_dynamic,
        matches_any_pattern,
        resolve_video_page,
        text_value,
        truncate,
        wrap_text,
    )
    from bili_models import Subscription

_FORWARD_FILTERS = frozenset({"forward", "forward_lottery", "lottery"})
_DRAW_FILTERS = frozenset({"draw", "lottery"})


# ───────────────────────── 视频消息链 ─────────────────────────


def build_video_text(
    info: dict[str, Any],
    ref: dict[str, Any],
    *,
    wrap_width: int = DYNAMIC_TEXT_WRAP_WIDTH,
) -> str:
    """根据视频信息与引用构建纯文本摘要（自动换行、无 emoji）。"""
    stat: dict[str, Any] = info["stat"] if isinstance(info.get("stat"), dict) else {}
    owner: dict[str, Any] = info["owner"] if isinstance(info.get("owner"), dict) else {}
    page, page_idx, page_count = resolve_video_page(info, ref)

    bvid = str(info.get("bvid") or ref.get("bvid") or "").strip()
    duration = page.get("duration") or info.get("duration") or 0

    lines = [
        clean_and_wrap(text_value(info.get("title")) or "未知标题", wrap_width),
        f"UP: {text_value(owner.get('name')) or '未知UP'}",
        f"播放: {format_count(stat.get('view'))}",
        f"点赞: {format_count(stat.get('like'))}",
        f"时长: {format_duration(duration)}",
    ]
    if page_count > 1:
        part = text_value(page.get("part"))
        lines.append(f"分P: {page_idx + 1}/{page_count} {part}".rstrip())
    desc = text_value(info.get("desc"))
    if desc:
        prefix = "简介: "
        desc_width = max(1, wrap_width - display_width(prefix)) if wrap_width > 0 else 0
        lines.append(prefix + wrap_text(truncate(desc, VIDEO_DESC_LIMIT), desc_width))
    if bvid:
        lines.append(VIDEO_DETAIL_URL.format(bvid=bvid))
    return "\n".join(lines)


def build_video_chain(
    info: dict[str, Any],
    ref: dict[str, Any],
    *,
    wrap_width: int = DYNAMIC_TEXT_WRAP_WIDTH,
) -> list[object]:
    """根据视频信息和引用构建消息链（封面 + 文本）。"""
    chain: list[object] = []
    cover = str(info.get("pic") or "").strip()
    if cover.startswith(("http://", "https://")):
        with suppress(TypeError, ValueError):  # pragma: no cover - 仅防御异常图片组件
            chain.append(Comp.Image.fromURL(cover))
    chain.append(Comp.Plain(build_video_text(info, ref, wrap_width=wrap_width)))
    return chain


# ───────────────────────── 动态消息链 ─────────────────────────


def dynamic_headline(item: dict[str, Any]) -> str:
    """动态推送的头部行：``[B站动态] UP主 · 类型``。"""
    return f"[B站动态] {dynamic_author_name(item)} · {dynamic_type_label(item)}"


def _dynamic_body_lines(item: dict[str, Any], wrap_width: int) -> list[str]:
    """各类型动态的正文段落（已清洗 emoji，已按列宽换行）。

    排版通用规则：**正文 → 卡片标题/简介 → 统计信息 → 转发原文**，
    同一内容在正文里出现过就不再重复占行（例如视频标题已含在正文中）。
    """
    lines: list[str] = []
    text = extract_dynamic_text(item)
    if text:
        lines.append(wrap_text(truncate(text, DYNAMIC_TEXT_LIMIT), wrap_width))

    card = dynamic_card(item)
    title = card["title"]
    if title and title not in text:
        lines.append(wrap_text(f"《{title}》", wrap_width))
    desc = card["desc"]
    if desc and desc not in text and card["kind"] != "视频":
        lines.append(wrap_text(truncate(desc, VIDEO_DESC_LIMIT), wrap_width))
    if card["stat"]:
        lines.append(card["stat"])

    if dynamic_type(item) == "DYNAMIC_TYPE_FORWARD" and dynamic_forward_orig(item):
        forward = extract_forward_text(item)
        if forward:
            lines.append(wrap_text(truncate(forward, DYNAMIC_TEXT_LIMIT), wrap_width))
        orig_card = dynamic_card(dynamic_forward_orig(item))
        if orig_card["stat"]:
            lines.append(f"原动态数据：{orig_card['stat']}")
    return [line for line in lines if line.strip()]


def _dynamic_link_lines(item: dict[str, Any]) -> list[str]:
    """链接块（带中文标签、无 emoji），按「视频 → 卡片 → 直播 → 动态 → 原文」排序。"""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, url: Any) -> None:
        text = str(url or "").strip()
        if is_http_url(text) and text not in seen:
            seen.add(text)
            entries.append((label, text))

    card = dynamic_card(item)
    bvid = str(dynamic_archive(item).get("bvid") or "").strip()
    if bvid:
        add("视频", VIDEO_DETAIL_URL.format(bvid=bvid))
    if card["url"]:
        add(card["kind"] or "详情", card["url"])
    if card["room"]:
        add("直播", LIVE_ROOM_URL.format(room_id=card["room"]))
    dyn_id = dynamic_id(item)
    if dyn_id:
        add("动态", DYNAMIC_DETAIL_URL.format(dyn_id=dyn_id))

    orig = dynamic_forward_orig(item)
    if orig:
        orig_card = dynamic_card(orig)
        orig_bvid = str(dynamic_archive(orig).get("bvid") or "").strip()
        if orig_bvid:
            add("原视频", VIDEO_DETAIL_URL.format(bvid=orig_bvid))
        if orig_card["url"]:
            add("原文", orig_card["url"])
        orig_id = dynamic_id(orig)
        if orig_id:
            add("原文", DYNAMIC_DETAIL_URL.format(dyn_id=orig_id))
    return [f"{label}：{url}" for label, url in entries]


def build_dynamic_chain(
    item: dict[str, Any],
    *,
    wrap_width: int = DYNAMIC_TEXT_WRAP_WIDTH,
) -> list[object]:
    """构建动态推送消息链：头部 + 正文（按类型排版）→ 图片 → 链接。

    排版顺序固定为「头部 → 正文 → 图片 → 链接」，避免多图把正文顶出屏幕；
    *wrap_width* 为正文自动换行的显示列宽（0 表示不换行）。
    """
    chain: list[object] = []
    body_lines = _dynamic_body_lines(item, wrap_width)
    text = "\n".join([dynamic_headline(item), *body_lines]) if body_lines else dynamic_headline(item)
    chain.append(Comp.Plain(text))

    for url in extract_dynamic_images(item):
        try:
            chain.append(Comp.Image.fromURL(url))
        except (TypeError, ValueError):  # pragma: no cover - 仅防御异常图片组件
            continue

    links = _dynamic_link_lines(item)
    if links:
        chain.append(Comp.Plain("\n".join(links)))
    return chain


def build_live_chain(
    live_info: dict[str, Any],
    *,
    now_live: bool,
    wrap_width: int = DYNAMIC_TEXT_WRAP_WIDTH,
) -> list[object]:
    """构建直播状态推送消息链（开播含封面，下播仅文本）。"""
    uname = text_value(live_info.get("uname")) or "UP主"
    room = str(live_info.get("room_id") or "").strip()

    chain: list[object] = []
    if not now_live:
        chain.append(Comp.Plain(f"[B站直播] {uname} 下播了"))
        return chain

    lines = [f"[B站直播] {uname} 开播了"]
    title = extract_live_title(live_info)
    if title:
        lines.append(wrap_text(title, wrap_width))
    chain.append(Comp.Plain("\n".join(lines)))
    cover = extract_live_cover(live_info)
    if cover:
        with suppress(TypeError, ValueError):  # pragma: no cover - 仅防御异常图片组件
            chain.append(Comp.Image.fromURL(cover))
    if room:
        chain.append(Comp.Plain(f"直播：{LIVE_ROOM_URL.format(room_id=room)}"))
    return chain


# ───────────────────────── 动态过滤 ─────────────────────────


def should_filter_dynamic(
    item: dict[str, Any],
    sub: Subscription,
    compiled_regex: Sequence[re.Pattern[str]] | None = None,
) -> bool:
    """判断该动态是否应被过滤（True = 过滤掉）。

    *compiled_regex* 为调用方预编译的正则（生产路径每次轮询只编译一次，
    避免热路径反复 ``re.search(str)`` 走 Python 内部缓存）。
    """
    filters = set(sub.filter_types or [])
    if not filters and not sub.filter_regex:
        return False

    dyn_type = str(item.get("type") or "")
    text = extract_dynamic_text(item)

    if dyn_type == "DYNAMIC_TYPE_FORWARD":
        if filters & _FORWARD_FILTERS:
            if "forward" in filters:
                return True
            if "forward_lottery" in filters and is_forward_lottery(item):
                return True
            if "lottery" in filters and is_lottery_text(text):
                return True
    elif dyn_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
        if filters & _DRAW_FILTERS:
            if "draw" in filters:
                return True
            if "lottery" in filters and is_lottery_text(text):
                return True
    elif (dyn_type == "DYNAMIC_TYPE_AV" and "video" in filters) or (
        dyn_type == "DYNAMIC_TYPE_ARTICLE" and "article" in filters
    ):
        return True

    if text and sub.filter_regex:
        patterns = compiled_regex if compiled_regex is not None else sub.filter_regex
        if matches_any_pattern(text, patterns):
            return True
    return False


# ───────────────────────── 订阅辅助 ─────────────────────────


def append_recent_id(sub: Subscription, dyn_id: str, limit: int) -> None:
    """把动态 ID 追加到去重缓存（LRU，先进先出）。"""
    if not dyn_id:
        return
    recent = sub.recent_ids
    if dyn_id in recent:
        recent.remove(dyn_id)
    recent.append(dyn_id)
    if limit <= 0:
        sub.recent_ids = []
    elif len(recent) > limit:
        sub.recent_ids = recent[-limit:]


def build_uid_targets(
    subscriptions: dict[str, list[Subscription]],
) -> dict[int, list[tuple[str, Subscription]]]:
    """按 UID 聚合所有会话的订阅，减少重复请求。"""
    targets: dict[int, list[tuple[str, Subscription]]] = {}
    for session, subs in subscriptions.items():
        for sub in subs:
            targets.setdefault(sub.uid, []).append((session, sub))
    return targets


def latest_dynamic_id(items: list[dict[str, Any]]) -> str:
    """取最新一条非置顶动态 ID（订阅锚点；置顶动态必须跳过）。"""
    for item in items:
        if not isinstance(item, dict) or is_top_dynamic(item):
            continue
        dyn_id = dynamic_id(item)
        if dyn_id:
            return dyn_id
    return ""


def collect_new_dynamics(
    items: list[dict[str, Any]],
    known: set[str],
) -> list[dict[str, Any]]:
    """按接口顺序（新 → 旧）收集未读动态，命中已知 ID 即停止。

    置顶动态会被跳过，但仍继续向后扫描，避免置顶导致漏推。
    """
    new_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or is_top_dynamic(item):
            continue
        dyn_id = dynamic_id(item)
        if not dyn_id or dyn_id in known:
            break
        new_items.append(item)
    return new_items


def pick_push_batch(new_items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """从「新 → 旧」的未读动态中挑选要推送的一批。

    取 **最新** 的 *limit* 条，并按时间正序返回，保证最新动态一定会被推送，
    且多条推送时阅读顺序自然。
    """
    if limit <= 0 or not new_items:
        return []
    return list(reversed(new_items[:limit]))
