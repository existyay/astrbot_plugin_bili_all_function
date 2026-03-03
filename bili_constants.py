BILI_BV_PATTERN = r"\bBV[0-9A-Za-z]{10}\b"
BILI_AV_PATTERN = r"\bav\d+\b"
BILI_SHORT_LINK_PATTERN = r"https?://(?:b23\.tv|bili2233\.cn)/[^\s'\"<>]+"
BILI_VIDEO_LINK_PATTERN = r"https?://(?:www\.|m\.)?bilibili\.com/video/[^\s'\"<>]+"
BILI_MESSAGE_PATTERN = (
    r"(?:"
    + BILI_BV_PATTERN
    + r"|"
    + BILI_AV_PATTERN
    + r"|"
    + BILI_SHORT_LINK_PATTERN
    + r"|"
    + BILI_VIDEO_LINK_PATTERN
    + r")"
)

VALID_FILTER_TYPES = {
    "forward",
    "lottery",
    "video",
    "article",
    "draw",
    "live",
    "forward_lottery",
}

QUALITY_TO_QN = {
    "360P": 16,
    "480P": 32,
    "720P": 64,
    "1080P": 80,
    "1080P+": 112,
    "1080P60": 116,
    "4K": 120,
}
