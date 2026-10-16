"""推送排版测试：emoji 清洗、显示列宽、自动换行、各类型动态专属排版。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from conftest import chain_text, count_images

import bili_constants as C
import bili_helpers as H
import bili_logic as L

#: 测试用的 emoji 一律写成转义（本插件源码/文档中不允许出现字面 emoji）。
HEART = "\U0001f493"  # 心形
RED_DOT = "\U0001f534"  # 红点
CHECK = "\u2705"  # 白框勾
CROSS = "\u274c"  # 叉
WARN = "\u26a0\ufe0f"  # 警告
STAR = "\u2b50"  # 星
SQUARE = "\u2b1b"  # 黑方块
MUSIC = "\U0001f3b5"  # 音符


def dyn(*, id_str: str = "1", type_: str = "DYNAMIC_TYPE_DRAW", major: dict | None = None, desc: str = "") -> dict:
    """构造最小可用的动态数据。"""
    dynamic: dict = {}
    if desc:
        dynamic["desc"] = {"text": desc}
    if major is not None:
        dynamic["major"] = major
    return {
        "id_str": id_str,
        "type": type_,
        "modules": {"module_author": {"name": "UP"}, "module_dynamic": dynamic},
    }


# ───────────────────────── emoji 清洗 ─────────────────────────


class TestStripEmoji:
    def test_removes_common_emoji(self):
        assert H.strip_emoji(f"你好{HEART}世界{RED_DOT}") == "你好世界"
        assert H.strip_emoji(f"{CHECK}完成{WARN}注意{CROSS}失败") == "完成注意失败"
        assert H.strip_emoji(f"星星{STAR}方块{SQUARE}") == "星星方块"

    def test_removes_variation_selector_and_zwj_sequences(self):
        assert H.strip_emoji("a\u2764\ufe0fb") == "ab"
        assert H.strip_emoji("family\U0001f468\u200d\U0001f469") == "family"
        assert H.strip_emoji("1\ufe0f\u20e3") == "1"

    def test_keeps_arrows_and_normal_text(self):
        """``→`` 是排版符号（中文文档常用），必须保留。"""
        assert H.strip_emoji("看这里→ https://b23.tv/x") == "看这里→ https://b23.tv/x"
        assert H.strip_emoji("普通文本 no emoji") == "普通文本 no emoji"

    def test_empty_input(self):
        assert H.strip_emoji("") == ""


class TestStripBiliEmojiTokens:
    def test_removes_tv_and_underscore_tokens(self):
        assert H.strip_bili_emoji_tokens("你好[tv_微笑]呀") == "你好呀"
        assert H.strip_bili_emoji_tokens("[2026嘉然的画册动态表情包_糖糖回家]来了") == "来了"

    def test_removes_well_known_names(self):
        assert H.strip_bili_emoji_tokens("好[doge]啊[大笑]") == "好啊"

    def test_keeps_normal_brackets(self):
        assert H.strip_bili_emoji_tokens("[公告] 与 [A-SOUL] 保留") == "[公告] 与 [A-SOUL] 保留"
        assert H.strip_bili_emoji_tokens("【重点】全角括号不受影响") == "【重点】全角括号不受影响"
        assert H.strip_bili_emoji_tokens("[2026] 与 [1_2]") == "[2026] 与 [1_2]"

    def test_keeps_bracket_without_content(self):
        assert H.strip_bili_emoji_tokens("[]_[_]") == "[]_[_]"

    def test_no_bracket_fast_path(self):
        assert H.strip_bili_emoji_tokens("没有中括号") == "没有中括号"

    def test_empty_input(self):
        assert H.strip_bili_emoji_tokens("") == ""


class TestCleanText:
    def test_collapses_blank_lines_and_spaces(self):
        assert H.clean_text("\n\n  开头  \n\n\n\n结尾   \n\n") == "开头\n\n结尾"

    def test_strips_spaces_next_to_cjk_punctuation(self):
        assert H.clean_text("保留， 与（ 括 ）") == "保留，与（括）"

    def test_removes_zero_width_and_normalizes_crlf(self):
        assert H.clean_text("a\r\nb\u200bc\ufeff") == "a\nbc"

    def test_keep_newlines_false(self):
        assert H.clean_text("第一行\n第二行", keep_newlines=False) == "第一行 第二行"

    def test_non_string_and_empty(self):
        assert H.clean_text("") == ""
        assert H.clean_text("   \n  ") == ""

    def test_combined_with_wrap(self):
        assert H.clean_and_wrap("中文" * 12, 8) == "中文中文\n中文中文\n中文中文\n中文中文\n中文中文\n中文中文"


class TestTextValue:
    def test_scalar_and_none(self):
        assert H.text_value(None) == ""
        assert H.text_value("文本") == "文本"
        assert H.text_value(123) == "123"

    def test_dict_picks_first_readable_field(self):
        assert H.text_value({"text": "正文"}) == "正文"
        assert H.text_value({"content": "内容"}) == "内容"
        assert H.text_value({"unknown": "x"}) == ""
        assert H.text_value({"text": {"text": "嵌套"}}) == "嵌套"

    def test_dict_skips_empty_earlier_fields(self):
        assert H.text_value({"text": "", "title": "标题"}) == "标题"
        assert H.text_value({"text": {"unknown": 1}, "name": "名字"}) == "名字"

    def test_list_of_dicts(self):
        assert H.text_value([{"text": "一"}, {"text": "二"}]) == "一\n二"
        assert H.text_value([{"unknown": 1}, "三"]) == "三"


class TestDisplayWidth:
    def test_widths(self):
        assert H.display_width("abc") == 3
        assert H.display_width("中文") == 4
        assert H.display_width("a中") == 3
        assert H.display_width("") == 0

    def test_combining_mark_counts_zero(self):
        assert H.display_width("e\u0301") == 1


# ───────────────────────── 自动换行 ─────────────────────────


class TestWrapText:
    def test_disabled_and_empty(self):
        assert H.wrap_text("很长的中文" * 20, 0) == "很长的中文" * 20
        assert H.wrap_text("", 10) == ""

    def test_short_line_untouched(self):
        assert H.wrap_text("短", 40) == "短"

    def test_cjk_breaks_by_display_width(self):
        lines = H.wrap_text("中文" * 10, 10).split("\n")
        assert lines == ["中文中文中", "文中文中文", "中文中文中", "文中文中文"]

    def test_ascii_keeps_spaces(self):
        wrapped = H.wrap_text("hello world this is a long english sentence", 15)
        assert " " in wrapped
        for line in wrapped.split("\n"):
            assert H.display_width(line) <= 15
        # 拆行时不会把两个单词粘在一起
        assert "helloworld" not in wrapped.replace("\n", " ")

    def test_never_splits_url(self):
        url = "https://www.bilibili.com/video/BV1Hw4m1r7dp"
        wrapped = H.wrap_text(f"看这个 {url} 很棒", 12)
        assert url in wrapped.split("\n")

    def test_at_and_topic_are_atomic(self):
        wrapped = H.wrap_text("转发 @乃琳Queen 的话题 #A-SOUL六周年# 内容", 12)
        assert "@乃琳Queen" in wrapped
        assert "#A-SOUL六周年#" in wrapped

    def test_no_line_starts_with_cjk_punctuation(self):
        wrapped = H.wrap_text("中文标点禁则测试，这里故意让断行落在标点附近。", 20)
        for line in wrapped.split("\n")[1:]:
            assert line[0] not in "，。！？；：、）】》」』…"

    def test_existing_newlines_are_kept(self):
        assert H.wrap_text("第一行\n第二行", 40) == "第一行\n第二行"

    def test_indent_applied_to_continuation_lines(self):
        wrapped = H.wrap_text("中文" * 12, 8, indent="  ")
        lines = wrapped.split("\n")
        assert not lines[0].startswith(" ")
        assert all(line.startswith("  ") for line in lines[1:])

    def test_single_oversized_atom_is_not_lost(self):
        assert H.wrap_text("https://example.com/" + "x" * 60, 10) == "https://example.com/" + "x" * 60


# ───────────────────────── 类型标签与卡片 ─────────────────────────


class TestDynamicTypeLabels:
    def test_every_known_type_has_a_label(self):
        for type_name, label in C.DYNAMIC_TYPE_LABELS.items():
            assert label
            assert H.dynamic_type_label({"type": type_name}) == label

    def test_unknown_and_invalid(self):
        assert H.dynamic_type_label({"type": "DYNAMIC_TYPE_NEW"}) == C.DEFAULT_DYNAMIC_TYPE_LABEL
        assert H.dynamic_type_label("bad") == C.DEFAULT_DYNAMIC_TYPE_LABEL

    def test_new_types_are_covered(self):
        for type_name in (
            "DYNAMIC_TYPE_LOTTERY",
            "DYNAMIC_TYPE_LIVE",
            "DYNAMIC_TYPE_COMMON_SQUARE",
            "DYNAMIC_TYPE_MEDIALIST",
            "DYNAMIC_TYPE_MUSIC",
            "DYNAMIC_TYPE_PGC",
            "DYNAMIC_TYPE_COURSES",
            "DYNAMIC_TYPE_NONE",
        ):
            assert type_name in C.DYNAMIC_TYPE_LABELS


class TestDynamicCard:
    def test_no_major(self):
        card = H.dynamic_card(dyn())
        assert card == {"kind": "", "title": "", "desc": "", "stat": "", "url": "", "room": ""}
        assert H.dynamic_card("bad")["kind"] == ""

    def test_archive_card(self):
        card = H.dynamic_card(
            dyn(
                type_="DYNAMIC_TYPE_AV",
                major={
                    "archive": {
                        "title": "视频标题",
                        "desc": "视频简介",
                        "bvid": "BV1",
                        "jump_url": "https://www.bilibili.com/video/BV1",
                        "stat": {"play": 12345, "danmaku": 67},
                        "duration_text": "01:05",
                    },
                },
            ),
        )
        assert card["kind"] == "视频"
        assert card["title"] == "视频标题"
        assert card["desc"] == "视频简介"
        assert card["stat"] == "播放 1.2万 | 弹幕 67 | 时长 01:05"
        assert card["url"] == "https://www.bilibili.com/video/BV1"

    def test_article_card(self):
        card = H.dynamic_card(
            dyn(
                type_="DYNAMIC_TYPE_ARTICLE",
                major={
                    "article": {
                        "title": "专栏标题",
                        "desc": "摘要",
                        "stats": {"view": 20000},
                        "jump_url": "https://b/1",
                    }
                },
            ),
        )
        assert (card["kind"], card["stat"], card["url"]) == ("专栏", "阅读 2.0万", "https://b/1")

    def test_article_card_falls_back_to_plain_view(self):
        card = H.dynamic_card(dyn(type_="DYNAMIC_TYPE_ARTICLE", major={"article": {"view": 5}}))
        assert card["stat"] == "阅读 5"

    def test_live_rcmd_card_expands_json_content(self):
        payload = {"live_play_info": {"title": "直播标题", "room_id": 7, "popularity": 10000, "watched_show": 3}}
        card = H.dynamic_card(
            dyn(
                type_="DYNAMIC_TYPE_LIVE_RCMD",
                major={"live_rcmd": {"content": json.dumps(payload, ensure_ascii=False)}},
            ),
        )
        assert card["kind"] == "直播"
        assert card["title"] == "直播标题"
        assert card["room"] == "7"
        assert card["stat"] == "人气 1.0万 | 看过 3"

    def test_live_rcmd_card_tolerates_bad_json(self):
        card = H.dynamic_card(dyn(type_="DYNAMIC_TYPE_LIVE_RCMD", major={"live_rcmd": {"content": "{不是json"}}))
        assert card["kind"] == "直播"
        assert card["stat"] == ""

    def test_live_play_info_card(self):
        card = H.dynamic_card(
            dyn(major={"live_play_info": {"title": "直播中", "link": "https://live.bilibili.com/9", "online": 20}}),
        )
        assert card["kind"] == "直播"
        assert card["url"] == "https://live.bilibili.com/9"
        assert card["stat"] == "人气 20"

    def test_medialist_card(self):
        card = H.dynamic_card(
            dyn(
                major={
                    "medialist": {
                        "title": "合集",
                        "desc": "合集简介",
                        "stat": {"count": 12},
                        "jump_url": "https://b/ml",
                    }
                }
            ),
        )
        assert (card["kind"], card["title"], card["desc"], card["stat"]) == ("合集", "合集", "合集简介", "内容 12")

    def test_other_cards_map_to_kinds(self):
        for major_key, kind in (
            ("common_square", "卡片"),
            ("music", "音乐"),
            ("pgc", "番剧"),
            ("courses", "课程"),
            ("upower_common_square", "充电"),
        ):
            card = H.dynamic_card(dyn(major={major_key: {"title": "标题", "jump_url": "https://b/x"}}))
            assert card["kind"] == kind
            assert card["url"] == "https://b/x"

    def test_common_card_reads_nested_square(self):
        card = H.dynamic_card(
            dyn(
                type_="DYNAMIC_TYPE_COMMON_SQUARE",
                major={"common": {"square": {"title": "活动标题", "desc": "活动说明", "jump_url": "https://b/sq"}}},
            ),
        )
        assert (card["kind"], card["title"], card["desc"], card["url"]) == (
            "卡片",
            "活动标题",
            "活动说明",
            "https://b/sq",
        )

    def test_lottery_card_is_labelled(self):
        card = H.dynamic_card(
            dyn(
                type_="DYNAMIC_TYPE_LOTTERY",
                major={"common": {"lottery": {"title": "抽奖", "jump_url": "https://b/l"}}},
            ),
        )
        assert card["kind"] == "抽奖"
        assert card["title"] == "抽奖"
        assert card["url"] == "https://b/l"

    def test_common_card_without_nested_payload(self):
        card = H.dynamic_card(dyn(major={"common": {"title": "扁平卡片", "jump_url": "https://b/flat"}}))
        assert card["kind"] == "卡片"
        assert card["title"] == "扁平卡片"

    def test_every_known_card_key_maps_to_a_kind(self):
        for major_key, (kind, _url_keys) in H._CARD_KINDS.items():
            card = H.dynamic_card(dyn(major={major_key: {"title": "标题"}}))
            assert card["kind"] == kind, major_key
            assert card["title"] == "标题", major_key

    def test_opus_card_has_no_url(self):
        card = H.dynamic_card(dyn(major={"opus": {"summary": {"text": "摘要"}}}))
        assert card["kind"] == "图文"
        assert card["desc"] == "摘要"
        assert card["url"] == ""

    def test_url_fallback_from_nested_field(self):
        card = H.dynamic_card(dyn(major={"music": {"name": "歌名", "url": "https://b/song"}}))
        assert card["title"] == "歌名"
        assert card["url"] == "https://b/song"

    def test_card_cleans_emoji_from_fields(self):
        card = H.dynamic_card(dyn(major={"music": {"name": f"歌名{HEART}", "desc": "简介[tv_微笑]"}}))
        assert card["title"] == "歌名"
        assert card["desc"] == "简介"

    def test_archive_card_without_stat(self):
        card = H.dynamic_card(dyn(type_="DYNAMIC_TYPE_AV", major={"archive": {"bvid": "BV1"}}))
        assert card["stat"] == ""

    def test_non_dict_major_entry_is_skipped(self):
        card = H.dynamic_card(dyn(major={"archive": "坏数据", "music": {"title": "歌"}}))
        assert card["kind"] == "音乐"
        assert card["title"] == "歌"

    def test_unknown_major_key_yields_empty_card(self):
        card = H.dynamic_card(dyn(major={"some_new_card": {"title": "标题"}}))
        assert card["kind"] == ""
        assert card["title"] == ""

    def test_medialist_without_count(self):
        card = H.dynamic_card(dyn(major={"medialist": {"title": "合集", "jump_url": "https://b/ml"}}))
        assert card["stat"] == ""
        assert card["url"] == "https://b/ml"

    def test_live_payload_ignores_plain_content(self):
        card = H.dynamic_card(dyn(type_="DYNAMIC_TYPE_LIVE_RCMD", major={"live_rcmd": {"content": "不是JSON"}}))
        assert card["kind"] == "直播"
        assert card["room"] == ""


class TestExtractLiveTitle:
    def test_prefers_title_then_alternatives(self):
        assert H.extract_live_title({"title": "标题"}) == "标题"
        assert H.extract_live_title({"title": "", "live_title": "副标题"}) == "副标题"
        assert H.extract_live_title({"room_name": "房间名"}) == "房间名"
        assert H.extract_live_title({"title": HEART}) == ""
        assert H.extract_live_title({}) == ""


class TestExtractedTextIsCleaned:
    def test_dynamic_text_has_no_emoji_or_tokens(self):
        item = dyn(desc=f"正文{HEART}内容[tv_微笑]结束")
        assert H.extract_dynamic_text(item) == "正文内容结束"

    def test_dict_valued_fields_never_leak_repr(self):
        item = {
            "id_str": "1",
            "type": "DYNAMIC_TYPE_DRAW",
            "modules": {
                "module_dynamic": {
                    "desc": {"text": "正文"},
                    "major": {
                        "opus": {
                            "summary": {
                                "text": "摘要",
                                "rich_text_nodes": [{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "摘要"}],
                            }
                        }
                    },
                },
            },
        }
        text = H.extract_dynamic_text(item)
        assert "{" not in text and "rich_text_nodes" not in text
        assert text == "正文\n摘要"

    def test_author_name_is_cleaned(self):
        item = dyn()
        item["modules"]["module_author"] = {"name": f"UP{HEART}主"}
        assert H.dynamic_author_name(item) == "UP主"


# ───────────────────────── 各类型动态排版 ─────────────────────────


class TestPerTypeLayout:
    def test_article_layout(self):
        chain = L.build_dynamic_chain(
            dyn(
                id_str="11",
                type_="DYNAMIC_TYPE_ARTICLE",
                desc="专栏说明",
                major={
                    "article": {
                        "title": "专栏标题",
                        "desc": "专栏摘要",
                        "stats": {"view": 9},
                        "jump_url": "https://b/a",
                    }
                },
            ),
        )
        lines = chain[0].text.split("\n")
        assert lines[0] == "[B站动态] UP · 专栏文章"
        assert "专栏说明" in lines
        assert "《专栏标题》" in lines
        assert "专栏摘要" in lines
        assert "阅读 9" in lines
        assert "专栏：https://b/a" in chain_text(chain)

    def test_card_title_and_desc_are_not_duplicated(self):
        chain = L.build_dynamic_chain(
            dyn(desc="专栏标题", major={"article": {"title": "专栏标题", "desc": "专栏标题"}}),
        )
        assert chain[0].text.count("专栏标题") == 1

    def test_live_rcmd_layout_has_room_link(self):
        payload = {"live_play_info": {"title": "开播啦", "room_id": 888, "popularity": 12}}
        chain = L.build_dynamic_chain(
            dyn(id_str="12", type_="DYNAMIC_TYPE_LIVE_RCMD", major={"live_rcmd": {"content": json.dumps(payload)}}),
        )
        text = chain_text(chain)
        assert "开播啦" in text
        assert "人气 12" in text
        assert "直播：https://live.bilibili.com/888" in text

    def test_forward_includes_original_card_links(self):
        orig = dyn(
            id_str="99",
            type_="DYNAMIC_TYPE_AV",
            major={
                "archive": {
                    "bvid": "BV1orig",
                    "jump_url": "https://www.bilibili.com/video/BV1orig",
                    "stat": {"play": 8},
                }
            },
        )
        item = dyn(id_str="13", type_="DYNAMIC_TYPE_FORWARD", desc="转发理由")
        item["orig"] = orig
        chain = L.build_dynamic_chain(item)
        text = chain_text(chain)
        assert "原动态数据：播放 8" in text
        assert "原视频：https://www.bilibili.com/video/BV1orig" in text
        # 转发动态最多两行链接：内容链接（原视频）+ 本动态永久链接
        assert "动态：https://t.bilibili.com/13" in text
        assert "原文：https://t.bilibili.com/99" not in text
        assert len([line for line in text.split("\n") if line.startswith(("原视频：", "原文：", "动态："))]) == 2

    def test_forward_without_original(self):
        chain = L.build_dynamic_chain(dyn(id_str="14", type_="DYNAMIC_TYPE_FORWARD", desc="纯转发"))
        assert chain_text(chain) == "[B站动态] UP · 转发动态\n纯转发\n动态：https://t.bilibili.com/14"

    def test_forward_original_without_id_uses_card_link(self):
        orig = dyn(id_str="", type_="DYNAMIC_TYPE_ARTICLE", major={"article": {"jump_url": "https://b/article"}})
        item = dyn(id_str="19", type_="DYNAMIC_TYPE_FORWARD")
        item["orig"] = orig
        text = chain_text(L.build_dynamic_chain(item))
        assert "原文：https://b/article" in text
        assert "原文：https://t.bilibili.com/" not in text

    def test_unknown_type_falls_back_to_plain_body(self):
        chain = L.build_dynamic_chain(dyn(id_str="15", type_="DYNAMIC_TYPE_MUSIC", desc="听歌"))
        text = chain_text(chain)
        assert "音乐动态" in text
        assert "听歌" in text
        assert "动态：https://t.bilibili.com/15" in text

    def test_render_contains_no_emoji(self):
        chain = L.build_dynamic_chain(
            dyn(id_str="16", desc="正文{HEART}[tv_微笑]结束", major={"music": {"title": f"歌{MUSIC}"}})
        )
        assert H.strip_emoji(chain_text(chain)) == chain_text(chain)
        assert "[tv_" not in chain_text(chain)

    def test_wrap_width_is_forwarded(self):
        long_text = "很长的正文" * 30
        narrow = L.build_dynamic_chain(dyn(id_str="17", desc=long_text), wrap_width=10)[0].text.split("\n")[1:]
        wide = L.build_dynamic_chain(dyn(id_str="18", desc=long_text), wrap_width=0)[0].text
        assert max(H.display_width(line) for line in narrow) <= 10
        assert len(wide.split("\n")) == 2

    def test_dynamic_headline(self):
        assert L.dynamic_headline(dyn()) == "[B站动态] UP · 图文动态"


class TestVideoAndLiveLayout:
    def test_video_text_is_wrapped_and_cleaned(self):
        info = {
            "title": f"标题{HEART}" + "很长" * 30,
            "desc": "简介[tv_微笑]" + "内容" * 40,
            "owner": {"name": "UP主"},
            "stat": {"view": 1, "like": 2},
            "duration": 60,
        }
        text = L.build_video_text(info, {"bvid": "BV1"}, wrap_width=20)
        assert "标题" in text
        assert HEART not in text and "[tv_" not in text
        assert all(H.display_width(line) <= 20 for line in text.split("\n") if not line.startswith("http"))
        assert text.rstrip().endswith("https://www.bilibili.com/video/BV1")

    def test_video_text_wrap_disabled(self):
        text = L.build_video_text({"title": "标题" * 40}, {}, wrap_width=0)
        assert len(text.split("\n")) == 5

    def test_live_chain_wraps_title_and_drops_emoji(self):
        chain = L.build_live_chain(
            {"uname": f"UP{HEART}", "title": "标题" * 40, "room_id": 5, "cover": "https://c/1.jpg"},
            now_live=True,
            wrap_width=10,
        )
        text = chain_text(chain)
        assert text.startswith("[B站直播] UP 开播了\n")
        body = text.split("\n")[1:]
        assert all(H.display_width(line) <= 10 for line in body if not line.startswith("直播："))
        assert "直播：https://live.bilibili.com/5" in text
        assert count_images(chain) == 1

    def test_live_chain_starting_info_without_title(self):
        chain = L.build_live_chain({"uname": "UP", "room_id": 6}, now_live=True, wrap_width=40)
        assert chain_text(chain) == "[B站直播] UP 开播了\n直播：https://live.bilibili.com/6"


# ───────────────────────── 链接去重与数量上限 ─────────────────────────


class TestLinkKey:
    def test_same_content_different_links_share_a_key(self):
        assert H.link_key("https://t.bilibili.com/1259644763297021968") == H.link_key(
            "https://www.bilibili.com/opus/1259644763297021968",
        )
        assert H.link_key("https://www.bilibili.com/video/BV1Hw4m1r7dp?spm_id_from=333.999") == H.link_key(
            "https://www.bilibili.com/video/BV1Hw4m1r7dp/",
        )

    def test_id_kinds_are_distinguished(self):
        cases = {
            "https://www.bilibili.com/video/BV1Hw4m1r7dp": "bv:1hw4m1r7dp",
            "https://www.bilibili.com/video/av123": "av:123",
            "https://t.bilibili.com/456": "dyn:456",
            "https://www.bilibili.com/read/cv789": "cv:789",
            "https://live.bilibili.com/22637261": "live:22637261",
            "https://www.bilibili.com/audio/au321": "au:321",
            "https://www.bilibili.com/bangumi/play/ep111": "bangumi:111",
            "https://www.bilibili.com/cheese/play/ss222": "cheese:222",
            "https://space.bilibili.com/946974/channel/collectiondetail?sid=333": "ml:333",
            "https://www.bilibili.com/medialist/detail/ml444": "ml:444",
            "https://space.bilibili.com/946974": "space:946974",
        }
        for url, expected in cases.items():
            assert H.link_key(url) == expected, url

    def test_unknown_host_falls_back_to_normalised_url(self):
        assert H.link_key("HTTPS://Example.COM/a/b/?x=1#frag") == "https://example.com/a/b"
        assert H.link_key("not-a-url") == "not-a-url"
        assert H.link_key("") == ""
        assert H.link_key(None) == ""


class TestLinkBlockHasNoDuplicates:
    def test_link_block_dedupes_same_content_and_caps_lines(self):
        # 视频链接来自 bvid、卡片 jump_url（带查询参数）与 opus 形式，三者应合并为一行
        item = dyn(
            id_str="21",
            type_="DYNAMIC_TYPE_AV",
            major={
                "archive": {
                    "bvid": "BV1aaaabbbb1",
                    "jump_url": "https://www.bilibili.com/video/BV1aaaabbbb1?spm_id_from=333.999",
                }
            },
        )
        lines = L._dynamic_link_lines(item)
        assert lines == [
            "视频：https://www.bilibili.com/video/BV1aaaabbbb1",
            "动态：https://t.bilibili.com/21",
        ]

    def test_dynamic_permalink_dedupes_opus_card_url(self):
        item = dyn(
            id_str="22", type_="DYNAMIC_TYPE_DRAW", major={"opus": {"jump_url": "https://www.bilibili.com/opus/22"}}
        )
        lines = L._dynamic_link_lines(item)
        assert lines == ["图文：https://www.bilibili.com/opus/22"]

    def test_more_specific_label_wins_for_same_content(self):
        item = dyn(
            id_str="23",
            type_="DYNAMIC_TYPE_AV",
            major={"archive": {"bvid": "BV1ccccdddd1", "jump_url": "https://www.bilibili.com/video/BV1ccccdddd1"}},
        )
        lines = L._dynamic_link_lines(item)
        assert lines[0].startswith("视频：")

    def test_room_link_is_kept_for_live_cards(self):
        item = dyn(id_str="24", type_="DYNAMIC_TYPE_LIVE", major={"live_play_info": {"room_id": 777, "title": "开播"}})
        assert L._dynamic_link_lines(item) == [
            "直播：https://live.bilibili.com/777",
            "动态：https://t.bilibili.com/24",
        ]

    def test_non_http_urls_are_ignored(self):
        item = dyn(id_str="25", major={"article": {"jump_url": "javascript:alert(1)"}})
        assert L._dynamic_link_lines(item) == ["动态：https://t.bilibili.com/25"]

    def test_link_already_shown_in_body_is_not_repeated(self):
        item = dyn(
            id_str="26",
            type_="DYNAMIC_TYPE_LIVE",
            desc="今天19:30，是在直播间哦~→ https://live.bilibili.com/777\n10月16日团综",
            major={"live_play_info": {"room_id": 777, "title": "开播"}},
        )
        assert L._dynamic_link_lines(item) == ["动态：https://t.bilibili.com/26"]

    def test_body_url_stops_before_cjk_text(self):
        assert C.RE_URL_IN_TEXT.findall("来这儿看 https://www.bilibili.com/video/BV1aaaabbbb1 一起玩") == [
            "https://www.bilibili.com/video/BV1aaaabbbb1"
        ]
        item = dyn(id_str="27", desc="主页 https://space.bilibili.com/946974、欢迎关注")
        assert L._dynamic_link_lines(item) == ["动态：https://t.bilibili.com/27"]

    def test_every_fixture_dynamic_has_at_most_two_unique_links(self):
        fixture_dir = Path(__file__).resolve().parent / "fixtures"
        checked = 0
        for path in sorted(fixture_dir.glob("dynamic_feed_*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for raw in (payload.get("data") or {}).get("items") or []:
                item = copy.deepcopy(raw)
                H.normalize_modules(item)
                lines = L._dynamic_link_lines(item)
                checked += 1
                assert len(lines) <= C.MAX_PUSH_LINKS, (path.name, lines)
                keys = [H.link_key(line.split("：", 1)[1]) for line in lines]
                assert len(set(keys)) == len(keys), (path.name, lines)
                for line in lines:
                    label, url = line.split("：", 1)
                    assert label and H.is_http_url(url) and not C.RE_EMOJI.search(line), line
        assert checked > 30
