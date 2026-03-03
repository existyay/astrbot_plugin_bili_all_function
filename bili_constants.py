"""B站相关常量与预编译正则。"""
from __future__ import annotations

import re

# ── 原始正则模式（供外部 fallback 引用） ──
BILI_BV_PATTERN = r"\bBV[0-9A-Za-z]{10}\b"
BILI_AV_PATTERN = r"\bav\d+\b"
BILI_SHORT_LINK_PATTERN = r"https?://(?:b23\.tv|bili2233\.cn)/[^\s'\"<>]+"
BILI_VIDEO_LINK_PATTERN = r"https?://(?:www\.|m\.)?bilibili\.com/video/[^\s'\"<>]+"
BILI_MESSAGE_PATTERN = (
    rf"(?:{BILI_BV_PATTERN}|{BILI_AV_PATTERN}"
    rf"|{BILI_SHORT_LINK_PATTERN}|{BILI_VIDEO_LINK_PATTERN})"
)

# ── 预编译正则（热路径使用，避免反复 compile） ──
RE_BV = re.compile(BILI_BV_PATTERN)
RE_AV = re.compile(BILI_AV_PATTERN, re.IGNORECASE)
RE_SHORT_LINK = re.compile(BILI_SHORT_LINK_PATTERN, re.IGNORECASE)
RE_VIDEO_LINK = re.compile(BILI_VIDEO_LINK_PATTERN, re.IGNORECASE)
RE_MESSAGE = re.compile(BILI_MESSAGE_PATTERN, re.IGNORECASE)
RE_URL = re.compile(r"https?://[^\s'\"<>]+")
RE_LOTTERY = re.compile(r"恭喜.*等\d+位同学中奖")

# ── 过滤类型（frozenset: 不可变，in 测试更快） ──
VALID_FILTER_TYPES: frozenset[str] = frozenset(
    {"forward", "lottery", "video", "article", "draw", "live", "forward_lottery"}
)

# ── 画质映射 ──
QUALITY_TO_QN: dict[str, int] = {
    "360P": 16, "480P": 32, "720P": 64,
    "1080P": 80, "1080P+": 112, "1080P60": 116, "4K": 120,
}
