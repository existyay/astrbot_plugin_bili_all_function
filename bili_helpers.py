"""纯工具函数：链接提取、格式化、动态数据解析、API 数据规范化。"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

try:
    from .bili_constants import (
        RE_AV, RE_BV, RE_LOTTERY, RE_SHORT_LINK, RE_VIDEO_LINK,
    )
except ImportError:
    from bili_constants import (
        RE_AV, RE_BV, RE_LOTTERY, RE_SHORT_LINK, RE_VIDEO_LINK,
    )


# ───────────────────────── 安全嵌套取值 ─────────────────────────

def dig(obj: Any, *keys: str, default: Any = None) -> Any:
    """沿 *keys* 逐层深入 dict，任一层非 dict / key 缺失即返回 *default*。"""
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k)
    return obj if obj is not None else default


# ───────────────────────── 链接与 ID 提取 ─────────────────────────

_FAST_KW = ("bilibili.com/video/", "b23.tv/", "bili2233.cn/", "bv", "av")


def extract_links_and_ids(text: str) -> list[str]:
    """从文本中提取 B站视频链接 / 短链 / BV / av 号，去重保序。"""
    if not text:
        return []
    low = text.lower()
    if not any(kw in low for kw in _FAST_KW):
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
    info: dict[str, Any], ref: dict[str, Any],
) -> tuple[dict[str, Any], int, int]:
    """从视频信息 + 引用中解析分P → (page_dict, page_index_0based, page_count)。"""
    pages = info.get("pages") or []
    page_count = max(len(pages), 1)
    page_idx = min(int(ref.get("page", 0) or 0), page_count - 1)
    page = pages[page_idx] if pages and page_idx < len(pages) else {}
    return page, page_idx, page_count


# ───────────────────────── 格式化 ─────────────────────────

def format_count(value: int) -> str:
    if value >= 1_0000_0000:
        return f"{value / 1_0000_0000:.1f}亿"
    if value >= 1_0000:
        return f"{value / 1_0000:.1f}万"
    return str(value)


def format_duration(seconds: int) -> str:
    m, s = divmod(max(seconds, 0), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def truncate(s: str, max_len: int, suffix: str = "...") -> str:
    """截断字符串到 *max_len*，超出部分用 *suffix* 替代。"""
    return s if len(s) <= max_len else s[:max_len] + suffix


# ───────────────────────── 动态数据提取 ─────────────────────────

def _http_url(obj: Any, *keys: str) -> str:
    """从 dict 中按 keys 顺序尝试取出首个 http(s) URL。"""
    if not isinstance(obj, dict):
        return ""
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v
    return ""


def extract_dynamic_cover(item: dict[str, Any]) -> str:
    """提取动态封面图 URL（视频封面 > 图文首图）。"""
    major = dig(item, "modules", "module_dynamic", "major", default={})
    if not isinstance(major, dict):
        return ""
    # 视频封面
    cover = _http_url(major.get("archive") or {}, "cover")
    if cover:
        return cover
    # 图文首图
    pics = dig(major, "opus", "pics", default=[])
    if isinstance(pics, list) and pics:
        first = pics[0]
        if isinstance(first, dict):
            return _http_url(first, "url", "img_src", "src")
        if isinstance(first, str) and first.startswith(("http://", "https://")):
            return first
    return ""


def extract_dynamic_images(item: dict[str, Any], *, limit: int = 9) -> list[str]:
    """提取动态中的所有图片 URL（视频封面 / 图文多图），最多 *limit* 张。"""
    urls: list[str] = []
    major = dig(item, "modules", "module_dynamic", "major", default={})
    if not isinstance(major, dict):
        return urls
    # 视频封面
    cover = _http_url(major.get("archive") or {}, "cover")
    if cover:
        urls.append(cover)
    # 图文多图
    pics = dig(major, "opus", "pics", default=[])
    if isinstance(pics, list):
        for p in pics:
            if len(urls) >= limit:
                break
            if isinstance(p, dict):
                u = _http_url(p, "url", "img_src", "src")
                if u and u not in urls:
                    urls.append(u)
            elif isinstance(p, str) and p.startswith(("http://", "https://")) and p not in urls:
                urls.append(p)
    return urls[:limit]


def is_top_dynamic(item: dict[str, Any]) -> bool:
    return dig(item, "modules", "module_tag", "text") == "置顶"


def extract_dynamic_text(item: dict[str, Any]) -> str:
    """提取动态可读文本（描述 + opus 摘要 + archive 标题/简介）。"""
    dynamic = dig(item, "modules", "module_dynamic", default={})
    if not isinstance(dynamic, dict):
        return ""
    parts: list[str] = []
    text = dig(dynamic, "desc", "text")
    if text:
        parts.append(str(text))
    major = dynamic.get("major") or {}
    if isinstance(major, dict):
        st = dig(major, "opus", "summary", "text")
        if st:
            parts.append(str(st))
        archive = major.get("archive") or {}
        if isinstance(archive, dict):
            for k in ("title", "desc"):
                v = archive.get(k)
                if v:
                    parts.append(str(v))
    return "\n".join(parts).strip()


def is_lottery_text(text: str) -> bool:
    if not text:
        return False
    return "互动抽奖" in text or bool(RE_LOTTERY.search(text))


def is_forward_lottery(item: dict[str, Any]) -> bool:
    nodes = dig(
        item, "orig", "modules", "module_dynamic", "major",
        "opus", "summary", "rich_text_nodes", default=[],
    )
    return (
        bool(nodes)
        and isinstance(nodes[0], dict)
        and nodes[0].get("text") == "互动抽奖"
    )


# ───────────────────────── 过滤参数解析 ─────────────────────────

def parse_filter_args(
    args: list[str], valid_types: frozenset[str],
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
    """将 desktop/v1/feed/space 返回的新格式规范化为旧格式。

    新 API  modules 是 list（每元素 {"module_type": "…", "module_xxx": {…}}）。
    旧代码 modules 是 dict：{"module_author": {…}, "module_dynamic": {…}, …}。
    同时做字段层级映射：
      author.user.{name,mid,face} → author.{name,mid,face}
      module_desc            → module_dynamic.desc
      dyn_draw.items[].src   → major.opus.pics[].url
      dyn_archive            → major.archive
      dyn_forward.item       → item["orig"]（递归）
    """
    raw = item.get("modules")

    # Step 1: list → dict
    if isinstance(raw, dict):
        merged = raw
    elif isinstance(raw, list):
        merged: dict[str, Any] = {}
        for entry in raw:
            if isinstance(entry, dict):
                for k, v in entry.items():
                    if k != "module_type" and isinstance(v, dict):
                        merged[k] = v
        item["modules"] = merged
    else:
        return

    # Step 2: author.user → author 顶层
    author = merged.get("module_author")
    if isinstance(author, dict):
        user = author.get("user")
        if isinstance(user, dict):
            for f in ("name", "mid", "face"):
                if not author.get(f):
                    author[f] = user.get(f)

    # Step 3: module_desc → module_dynamic.desc
    desc_mod = merged.get("module_desc")
    dyn_mod = merged.get("module_dynamic")
    if isinstance(desc_mod, dict) and isinstance(dyn_mod, dict) and "desc" not in dyn_mod:
        dyn_mod["desc"] = desc_mod

    # Step 4: 新字段 → 旧 major 结构
    if isinstance(dyn_mod, dict) and "major" not in dyn_mod:
        major: dict[str, Any] = {}
        # 图文: dyn_draw → major.opus.pics
        dyn_draw = dyn_mod.get("dyn_draw")
        if isinstance(dyn_draw, dict):
            pics = [
                {"url": d["src"], "width": d.get("width", 0), "height": d.get("height", 0)}
                for d in (dyn_draw.get("items") or [])
                if isinstance(d, dict) and d.get("src")
            ]
            if pics:
                major["opus"] = {
                    "pics": pics,
                    "summary": desc_mod if isinstance(desc_mod, dict) else {},
                }
        # 视频: dyn_archive → major.archive
        dyn_archive = dyn_mod.get("dyn_archive")
        if isinstance(dyn_archive, dict):
            major["archive"] = dyn_archive
        if major:
            dyn_mod["major"] = major

        # Step 5: 转发 dyn_forward → item["orig"]（递归）
        dyn_forward = dyn_mod.get("dyn_forward")
        if isinstance(dyn_forward, dict):
            orig = dyn_forward.get("item")
            if isinstance(orig, dict):
                normalize_modules(orig)
                item["orig"] = orig
