"""``bili_helpers`` 纯函数测试。"""

from __future__ import annotations

import pytest
from conftest import deep_copy, feed_items, load_fixture

import bili_constants as C
import bili_helpers as H


class TestDig:
    def test_nested(self):
        assert H.dig({"a": {"b": {"c": 1}}}, "a", "b", "c") == 1

    def test_missing_and_non_dict(self):
        assert H.dig({"a": 1}, "a", "b", default="x") == "x"
        assert H.dig(None, "a") is None
        assert H.dig({"a": None}, "a", default=7) == 7

    def test_no_keys(self):
        assert H.dig({"a": 1}) == {"a": 1}


class TestUrlHelpers:
    def test_first_http_url(self):
        assert H.first_http_url({"a": "not-a-url", "b": "https://x/1"}, "a", "b") == "https://x/1"
        assert H.first_http_url({"b": {"src": "http://y/2"}}, "b") == "http://y/2"
        assert H.first_http_url({"b": {"url": "https://y/3"}}, "b") == "https://y/3"
        assert H.first_http_url({"b": {"src": 5}}, "b") == ""
        assert H.first_http_url("nope", "a") == ""
        assert H.first_http_url({}, "a") == ""

    def test_is_http_url(self):
        assert H.is_http_url("https://a")
        assert not H.is_http_url("ftp://a")
        assert not H.is_http_url(None)


class TestExtractLinksAndIds:
    def test_empty_and_fast_reject(self):
        assert H.extract_links_and_ids("") == []
        assert H.extract_links_and_ids("今天天气不错") == []

    def test_mixed_tokens_dedup(self):
        text = "看 https://www.bilibili.com/video/BV1xx411c7mD?p=3 和 BV1xx411c7mD 还有 av999 \
               以及 https://b23.tv/abcd"
        got = H.extract_links_and_ids(text)
        assert got[0] == "https://www.bilibili.com/video/BV1xx411c7mD?p=3"
        assert "av999" in got
        assert "https://b23.tv/abcd" in got
        assert got.count("BV1xx411c7mD") == 1


class TestParsePageIndex:
    def test_ok(self):
        assert H.parse_page_index("https://x/video/BV1?p=3") == 2

    def test_defaults(self):
        assert H.parse_page_index("https://x/video/BV1") == 0
        assert H.parse_page_index("https://x/video/BV1?p=0") == 0
        assert H.parse_page_index("https://x/video/BV1?p=abc") == 0
        assert H.parse_page_index(None) == 0


class TestResolveVideoPage:
    def test_list_pages(self):
        info = {"pages": [{"cid": 1}, {"cid": 2}]}
        page, idx, count = H.resolve_video_page(info, {"page": 1})
        assert (page["cid"], idx, count) == (2, 1, 2)

    def test_clamps_and_fallbacks(self):
        info = {"pages": [{"cid": 1}]}
        assert H.resolve_video_page(info, {"page": 9})[1] == 0
        assert H.resolve_video_page(info, {"page": -3})[1] == 0
        assert H.resolve_video_page(info, {"page": "bad"})[1] == 0
        assert H.resolve_video_page({}, {}) == ({}, 0, 1)
        assert H.resolve_video_page({"pages": "bad"}, {}) == ({}, 0, 1)
        assert H.resolve_video_page({"pages": ["bad"]}, {})[0] == {}
        assert H.resolve_video_page(None, {"page": None})[2] == 1


class TestParseCount:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, 1),
            (False, 0),
            (123, 123),
            (2.6, 3),
            ("1234", 1234),
            ("1,234", 1234),
            ("2.5万", 25000),
            ("1.1亿", 110000000),
            ("3万", 30000),
            ("12+", 12),
            ("", 0),
            ("-", 0),
            ("--", 0),
            ("null", 0),
            ("nan", 0),
            ("abc", 0),
            (None, 0),
            ("2.5 万", 25000),
        ],
    )
    def test_values(self, value, expected):
        assert H.parse_count(value) == expected

    def test_custom_default(self):
        assert H.parse_count("bad", 42) == 42


class TestFormatCount:
    def test_ranges(self):
        assert H.format_count(999) == "999"
        assert H.format_count(25000) == "2.5万"
        assert H.format_count(110000000) == "1.1亿"
        assert H.format_count("2.5万") == "2.5万"
        assert H.format_count(True) == "1"
        assert H.format_count(None) == "0"


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0:00"),
            (59, "0:59"),
            (60, "1:00"),
            (3661, "1:01:01"),
            ("3:20", "3:20"),
            ("", "0:00"),
            ("95", "1:35"),
            (None, "0:00"),
            ("abc", "0:00"),
        ],
    )
    def test_values(self, value, expected):
        assert H.format_duration(value) == expected


class TestTruncate:
    def test_behaviour(self):
        assert H.truncate("abc", 5) == "abc"
        assert H.truncate("abcdef", 3) == "abc..."
        assert H.truncate("abcdef", 0) == ""


class TestDynamicBasics:
    def test_dynamic_id(self):
        assert H.dynamic_id({"id_str": " 123 "}) == "123"
        assert H.dynamic_id({"id_str": None}) == ""
        assert H.dynamic_id("nope") == ""

    def test_modules_of_list_and_dict(self):
        as_dict = {"modules": {"module_author": {"name": "a"}}}
        as_list = {
            "modules": [
                {"module_type": "MODULE_TYPE_AUTHOR", "module_author": {"name": "a"}},
                "bad",
                {"module_type": "MODULE_TYPE_X", "module_x": "not-a-dict"},
            ],
        }
        assert H.modules_of(as_dict) == {"module_author": {"name": "a"}}
        assert H.modules_of(as_list) == {"module_author": {"name": "a"}}
        assert H.modules_of({}) == {}
        assert H.modules_of(None) == {}

    def test_is_top_dynamic(self):
        assert H.is_top_dynamic({"modules": {"module_author": {"is_top": True}}})
        assert H.is_top_dynamic({"modules": {"module_tag": {"text": "置顶"}}})
        assert H.is_top_dynamic({"basic": {"is_top": True}})
        assert not H.is_top_dynamic({"modules": {"module_author": {"is_top": False}}})
        assert not H.is_top_dynamic({"modules": {"module_tag": {"text": "其他"}}})
        assert not H.is_top_dynamic(None)

    def test_author_helpers(self):
        item = {"modules": {"module_author": {"name": "影视飓风", "mid": 946974, "pub_ts": 1700000000}}}
        assert H.dynamic_author_name(item) == "影视飓风"
        assert H.dynamic_author_uid(item) == "946974"
        assert H.dynamic_pub_ts(item) == 1700000000
        assert H.dynamic_author_name({"modules": {"module_author": {"mid": 5}}}) == "UID 5"
        assert H.dynamic_author_name({"modules": {"module_author": {}}}) == "UP主"
        assert H.dynamic_author_name({}) == "UP主"
        assert H.dynamic_author_uid({}) == ""
        assert H.dynamic_pub_ts({}) == 0
        assert H.dynamic_pub_ts({"modules": {"module_author": {"pub_ts": "1.5万"}}}) == 15000

    def test_type_label(self):
        assert H.dynamic_type_label({"type": "DYNAMIC_TYPE_AV"}) == "视频动态"
        assert H.dynamic_type_label({"type": "UNKNOWN"}) == C.DEFAULT_DYNAMIC_TYPE_LABEL
        assert H.dynamic_type_label(None) == C.DEFAULT_DYNAMIC_TYPE_LABEL

    def test_major_accessors(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "major": {
                        "archive": {"bvid": "BV1"},
                        "opus": {"pics": []},
                    },
                },
            },
        }
        assert H.dynamic_archive(item)["bvid"] == "BV1"
        assert H.dynamic_opus(item) == {"pics": []}
        assert H.dynamic_major({"modules": {"module_dynamic": {"major": "bad"}}}) == {}
        assert H.dynamic_archive({}) == {}
        assert H.dynamic_opus({}) == {}
        assert H.dynamic_major({}) == {}
        assert H.dynamic_forward_orig({"orig": {"id_str": "1"}}) == {"id_str": "1"}
        assert H.dynamic_forward_orig({"orig": "bad"}) == {}


class TestImages:
    def test_archive_cover_first(self):
        item = {"modules": {"module_dynamic": {"major": {"archive": {"cover": "https://c/1.jpg"}}}}}
        assert H.extract_dynamic_images(item) == ["https://c/1.jpg"]
        assert H.extract_dynamic_cover(item) == "https://c/1.jpg"

    def test_opus_pics_dict_and_str(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "major": {"opus": {"pics": [{"url": "https://p/1.jpg"}, "https://p/2.jpg", "bad"]}},
                },
            },
        }
        assert H.extract_dynamic_images(item) == ["https://p/1.jpg", "https://p/2.jpg"]

    def test_limit_and_zero(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "major": {"opus": {"pics": [{"url": f"https://p/{i}.jpg"} for i in range(5)]}},
                },
            },
        }
        assert len(H.extract_dynamic_images(item, limit=2)) == 2
        assert H.extract_dynamic_images(item, limit=0) == []

    def test_rich_text_images(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "desc": {
                        "text": "带图",
                        "rich_text_nodes": [
                            {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "x"},
                            {"type": "RICH_TEXT_NODE_TYPE_IMAGE", "image_src": "https://n/1.jpg"},
                            {"type": "RICH_TEXT_NODE_TYPE_IMAGE", "image_src": "https://n/1.jpg"},
                            {"type": "RICH_TEXT_NODE_TYPE_IMAGE", "src": "https://n/2.jpg"},
                            {"type": "RICH_TEXT_NODE_TYPE_IMAGE", "image_src": "not-url"},
                            "bad",
                        ],
                    },
                },
            },
        }
        assert H.extract_dynamic_images(item) == ["https://n/1.jpg", "https://n/2.jpg"]

    def test_forward_fallback(self):
        item = {
            "orig": {"modules": {"module_dynamic": {"major": {"archive": {"cover": "https://o/1.jpg"}}}}},
            "modules": {"module_dynamic": {}},
        }
        assert H.extract_dynamic_images(item) == ["https://o/1.jpg"]
        assert H.extract_dynamic_cover({"modules": {"module_dynamic": {}}}) == ""

    def test_break_when_limit_reached_by_rich_text(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "major": {"opus": {"pics": [{"url": "https://p/1.jpg"}]}},
                    "desc": {
                        "rich_text_nodes": [{"type": "IMAGE", "image_src": f"https://n/{i}.jpg"} for i in range(4)],
                    },
                },
            },
        }
        assert H.extract_dynamic_images(item, limit=2) == ["https://p/1.jpg", "https://n/0.jpg"]


class TestDynamicText:
    def test_desc_summary_archive_dedup(self):
        item = {
            "modules": {
                "module_dynamic": {
                    "desc": {"text": "正文"},
                    "major": {
                        "opus": {"summary": {"text": "摘要"}},
                        "archive": {"title": "标题", "desc": "正文"},
                    },
                },
            },
        }
        assert H.extract_dynamic_text(item) == "正文\n摘要\n标题"

    def test_empty(self):
        assert H.extract_dynamic_text({}) == ""
        assert H.extract_dynamic_text({"modules": {"module_dynamic": "bad"}}) == ""

    def test_extract_forward_text(self):
        item = {
            "orig": {
                "modules": {
                    "module_author": {"name": "原作者"},
                    "module_dynamic": {"desc": {"text": "原文"}},
                },
            },
        }
        assert H.extract_forward_text(item) == "@原作者: 原文"
        assert H.extract_forward_text({}) == ""
        assert H.extract_forward_text({"orig": {"modules": {}}}) == ""

    def test_live_cover_priority(self):
        assert H.extract_live_cover({"user_cover": "https://u/1.jpg", "cover": "https://c/2.jpg"}) == "https://u/1.jpg"
        assert H.extract_live_cover({"keyframe": "https://k/3.jpg"}) == "https://k/3.jpg"
        assert H.extract_live_cover({}) == ""

    def test_lottery(self):
        assert H.is_lottery_text("互动抽奖进行中")
        assert H.is_lottery_text("恭喜 @a 等3位同学中奖")
        assert not H.is_lottery_text("")
        assert not H.is_lottery_text("普通内容")

    def test_is_forward_lottery(self):
        nodes = {
            "orig": {
                "modules": {
                    "module_dynamic": {
                        "major": {"opus": {"summary": {"rich_text_nodes": [{"text": "互动抽奖"}]}}},
                    },
                },
            },
        }
        assert H.is_forward_lottery(nodes)
        by_type = {"orig": {"modules": {"module_dynamic": {"type": "DYNAMIC_TYPE_LOTTERY"}}}}
        assert H.is_forward_lottery(by_type)
        assert not H.is_forward_lottery({})
        assert not H.is_forward_lottery({"orig": {"modules": {"module_dynamic": {"type": "AV"}}}})


class TestParseFilterArgs:
    def test_split_and_dedup(self):
        types, regex = H.parse_filter_args(
            ["video", " lottery ", "", "抽奖", "video", ".*限定.*"],
            C.VALID_FILTER_TYPES,
        )
        assert types == ["video", "lottery"]
        assert regex == ["抽奖", ".*限定.*"]

    def test_none_valid(self):
        types, regex = H.parse_filter_args(["a", "b"], frozenset())
        assert types == []
        assert regex == ["a", "b"]


class TestNormalizeModules:
    def test_non_dict_item(self):
        assert H.normalize_modules("bad") is None

    def test_no_modules(self):
        item: dict = {}
        H.normalize_modules(item)
        assert "modules" not in item

    def test_author_user_flatten_and_idempotent(self):
        item = {
            "modules": {
                "module_author": {"mid": 1, "user": {"name": "名字", "face": "https://f/1.jpg", "mid": 2}},
            },
        }
        H.normalize_modules(item)
        author = item["modules"]["module_author"]
        assert author["name"] == "名字"
        assert author["face"] == "https://f/1.jpg"
        assert author["mid"] == 1  # 已有值不被覆盖
        H.normalize_modules(item)  # 幂等
        assert item["modules"]["module_author"]["name"] == "名字"

    def test_desc_only_creates_module_dynamic(self):
        """回归：只有 MODULE_TYPE_DESC 的动态正文不能丢。"""
        item = {
            "modules": [
                {"module_type": "MODULE_TYPE_AUTHOR", "module_author": {"user": {"name": "UP"}}},
                {
                    "module_type": "MODULE_TYPE_DESC",
                    "module_desc": {
                        "text": "纯文字动态",
                        "rich_text_nodes": [{"type": "RICH_TEXT_NODE_TYPE_IMAGE", "image_src": "https://i/1.jpg"}],
                    },
                },
            ],
        }
        H.normalize_modules(item)
        assert item["modules"]["module_dynamic"]["desc"]["text"] == "纯文字动态"
        assert H.extract_dynamic_text(item) == "纯文字动态"
        assert H.extract_dynamic_images(item) == ["https://i/1.jpg"]

    def test_desc_without_dynamic_and_without_desc_returns(self):
        item = {"modules": [{"module_type": "MODULE_TYPE_AUTHOR", "module_author": {}}]}
        H.normalize_modules(item)
        assert "module_dynamic" not in item["modules"]

    def test_dyn_draw_to_opus(self):
        item = {
            "modules": [
                {
                    "module_type": "MODULE_TYPE_DYNAMIC",
                    "module_dynamic": {
                        "type": "DYNAMIC_TYPE_DRAW",
                        "dyn_draw": {"items": [{"src": "https://d/1.jpg", "width": 1, "height": 2}, "bad"]},
                    },
                },
                {"module_type": "MODULE_TYPE_DESC", "module_desc": {"text": "文字"}},
            ],
        }
        H.normalize_modules(item)
        major = item["modules"]["module_dynamic"]["major"]
        assert major["opus"]["pics"] == [{"url": "https://d/1.jpg", "width": 1, "height": 2}]
        assert major["opus"]["summary"]["text"] == "文字"

    def test_dyn_archive_and_forward(self):
        item = {
            "modules": [
                {
                    "module_type": "MODULE_TYPE_DYNAMIC",
                    "module_dynamic": {
                        "type": "DYNAMIC_TYPE_FORWARD",
                        "dyn_archive": {"bvid": "BV1"},
                        "dyn_forward": {
                            "item": {
                                "id_str": "999",
                                "modules": [
                                    {"module_type": "MODULE_TYPE_AUTHOR", "module_author": {"user": {"name": "原"}}},
                                    {"module_type": "MODULE_TYPE_DESC", "module_desc": {"text": "原动态"}},
                                ],
                            },
                        },
                    },
                },
            ],
        }
        H.normalize_modules(item)
        dyn = item["modules"]["module_dynamic"]
        assert dyn["major"]["archive"]["bvid"] == "BV1"
        assert item["orig"]["id_str"] == "999"
        assert item["orig"]["modules"]["module_dynamic"]["desc"]["text"] == "原动态"

    def test_existing_major_not_overwritten(self):
        item = {"modules": {"module_dynamic": {"major": {"archive": {"bvid": "BV0"}}}}}
        H.normalize_modules(item)
        assert item["modules"]["module_dynamic"]["major"]["archive"]["bvid"] == "BV0"

    def test_draw_without_valid_items_falls_back(self):
        item = {
            "modules": {
                "module_dynamic": {"dyn_draw": {"items": ["bad"]}},
                "module_desc": {"rich_text_nodes": [{"type": "IMAGE", "image_src": "https://i/x.jpg"}]},
            },
        }
        H.normalize_modules(item)
        assert item["modules"]["module_dynamic"]["major"]["opus"]["pics"] == [{"url": "https://i/x.jpg"}]

    def test_dyn_draw_non_dict_ignored(self):
        item = {"modules": {"module_dynamic": {"dyn_draw": "bad"}}}
        H.normalize_modules(item)
        assert "major" not in item["modules"]["module_dynamic"]


class TestRealFixtures:
    """用线上快照验证解析结果（快照保存于 tests/fixtures）。"""

    def test_feeds_have_expected_shapes(self):
        for name in ("dynamic_feed_asoul.json", "dynamic_feed_jiaran.json", "dynamic_feed_yingshi.json"):
            payload = load_fixture(name)
            assert payload["code"] == 0
            assert payload["data"]["items"]

    def test_asoul_pinned_and_desc_only(self):
        """置顶动态只有 MODULE_TYPE_DESC（无 module_dynamic），正文与图片解析不能丢内容。"""
        items = feed_items("dynamic_feed_asoul.json")
        for item in items:
            H.normalize_modules(item)
        assert H.is_top_dynamic(items[0]), "首条应为置顶动态"
        pinned = items[0]
        assert "module_dynamic" in pinned["modules"]
        assert H.extract_dynamic_text(pinned), "置顶图文动态应能提取正文"
        assert H.dynamic_author_name(pinned) == "A-SOUL_Official"

        newest = next(it for it in items if not H.is_top_dynamic(it))
        assert len(H.extract_dynamic_images(newest)) >= 1
        assert H.is_http_url(H.extract_dynamic_cover(newest))

    def test_jiaran_archive_counts(self):
        items = feed_items("dynamic_feed_jiaran.json")
        for item in items:
            H.normalize_modules(item)
        archives = [H.dynamic_archive(it) for it in items]
        archives = [a for a in archives if a]
        assert archives, "快照中应包含视频动态"
        archive = archives[0]
        assert archive["bvid"].startswith("BV")
        assert H.parse_count(archive["stat"]["play"]) > 0
        assert H.format_duration(archive["duration_text"])

    def test_yingshi_forward_has_orig(self):
        items = feed_items("dynamic_feed_yingshi.json")
        for item in items:
            H.normalize_modules(item)
        forwards = [it for it in items if H.dynamic_forward_orig(it)]
        assert forwards, "快照中应包含转发动态"
        assert H.extract_forward_text(forwards[0])

    def test_normalization_is_idempotent(self):
        items = feed_items("dynamic_feed_jiaran.json")
        first = deep_copy(items)
        for item in first:
            H.normalize_modules(item)
        snapshot = deep_copy(first)
        for item in first:
            H.normalize_modules(item)
        assert first == snapshot
