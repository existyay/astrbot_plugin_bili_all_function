"""插件配置解析与内部工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

import bili_constants as C
import main as M
from bili_constants import MAX_FILTER_REGEX_LEN, MAX_UID
from bili_models import Subscription


class TestScalarCoercion:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (5, 5),
            ("5", 5),
            ("abc", 7),
            (None, 7),
            (0, 7),
            (-1, 7),
            (float("nan"), 7),
        ],
    )
    def test_int(self, value, expected):
        assert M.BiliAllPlugin._int(value, 7) == expected

    def test_int_with_zero_min(self):
        assert M.BiliAllPlugin._int("0", 5, min_val=0) == 0
        assert M.BiliAllPlugin._int("abc", 5, min_val=0) == 5

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(1.5, 1.5), ("1.5", 1.5), ("abc", 0.5), (None, 0.5), (-2, 0.5)],
    )
    def test_float(self, value, expected):
        assert M.BiliAllPlugin._float(value, 0.5) == expected

    def test_str_list(self):
        assert M.BiliAllPlugin._str_list("1, 2 3\n4") == ["1", "2", "3", "4"]
        assert M.BiliAllPlugin._str_list(["1", " 2 ", "", None]) == ["1", "2", "None"]
        assert M.BiliAllPlugin._str_list("") == []
        assert M.BiliAllPlugin._str_list(None) == []
        assert M.BiliAllPlugin._str_list(5) == []


class TestCfg:
    def test_nested_read(self, plugin_factory):
        plugin = plugin_factory({"a": {"b": {"c": 3}}})
        assert plugin._cfg("a.b.c") == 3
        assert plugin._cfg("a.b", "fallback") == {"c": 3}
        assert plugin._cfg("a.x", "fallback") == "fallback"
        assert plugin._cfg("a.b.c.d", "fallback") == "fallback"

    def test_non_dict_node(self, plugin_factory):
        plugin = plugin_factory({"a": 5})
        assert plugin._cfg("a.b", "fallback") == "fallback"
        assert plugin._cfg("missing", None) is None


class TestRefreshConfig:
    def test_defaults(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin.enable_bili_parse is True
        assert plugin.send_video_info is True
        assert plugin.enable_llm_tool is True
        assert plugin.poll_interval_seconds == C.DEFAULT_POLL_INTERVAL_SECONDS
        assert plugin.timeout_seconds == C.DEFAULT_TIMEOUT_SECONDS
        assert plugin.dynamic_limit == C.DEFAULT_DYNAMIC_LIMIT
        assert plugin.uid_concurrency == C.DEFAULT_UID_CONCURRENCY
        assert plugin.uid_task_gap_seconds == C.DEFAULT_UID_TASK_GAP_SECONDS
        assert plugin.recent_ids_limit == C.DEFAULT_RECENT_IDS_LIMIT
        assert plugin.empty_retry_count == C.DEFAULT_EMPTY_RETRY_COUNT
        assert plugin.http_retry_count == C.DEFAULT_HTTP_RETRY_COUNT
        assert plugin.video_quality_label == C.DEFAULT_QUALITY_LABEL
        assert plugin.enable_video_download is False
        assert plugin.video_auto_delete is True
        assert plugin.bili_cookie == ""
        assert plugin.admin_ids == set()

    def test_full_custom(self, plugin_factory):
        plugin = plugin_factory(
            {
                "features": {"enable_bili_parse": False, "send_video_info": False, "enable_llm_tool": False},
                "subscription": {
                    "poll_interval_seconds": 120,
                    "timeout_seconds": 30,
                    "dynamic_limit": 5,
                    "uid_concurrency": 8,
                    "uid_task_gap_seconds": 2,
                    "recent_ids_limit": 50,
                    "empty_retry_count": 4,
                    "empty_retry_delay_seconds": 1.5,
                    "http_retry_count": 3,
                    "http_retry_delay_seconds": 0.9,
                },
                "video_download": {
                    "enable": True,
                    "quality": "1080p",  # 小写也应被接受
                    "max_duration_seconds": 120,
                    "max_size_mb": 20,
                    "auto_delete": False,
                    "retry_count": 1,
                    "bili_cookie": "  SESSDATA=abc;  ",
                },
                "permission": {"admin_ids": "1, 2\n3"},
            }
        )
        assert plugin.enable_bili_parse is False
        assert plugin.send_video_info is False
        assert plugin.enable_llm_tool is False
        assert plugin.poll_interval_seconds == 120
        assert plugin.timeout_seconds == 30
        assert plugin.dynamic_limit == 5
        assert plugin.uid_concurrency == 8
        assert plugin.uid_task_gap_seconds == 2
        assert plugin.recent_ids_limit == 50
        assert plugin.empty_retry_count == 4
        assert plugin.empty_retry_delay_seconds == 1.5
        assert plugin.http_retry_count == 3
        assert plugin.http_retry_delay_seconds == 0.9
        assert plugin.video_quality_label == "1080P"
        assert plugin.video_max_duration_seconds == 120
        assert plugin.video_max_size_mb == 20
        assert plugin.video_auto_delete is False
        assert plugin.video_retry_count == 1
        assert plugin.bili_cookie == "SESSDATA=abc;"
        assert plugin.admin_ids == {"1", "2", "3"}

    def test_invalid_values_fall_back(self, plugin_factory):
        plugin = plugin_factory(
            {
                "subscription": {
                    "poll_interval_seconds": "abc",
                    "timeout_seconds": 0,
                    "dynamic_limit": -5,
                    "uid_concurrency": None,
                    "uid_task_gap_seconds": "x",
                    "empty_retry_count": -1,
                    "http_retry_count": "x",
                },
                "video_download": {"quality": "不存在的画质", "max_duration_seconds": "x"},
                "permission": {"admin_ids": 123},
            }
        )
        assert plugin.poll_interval_seconds == C.DEFAULT_POLL_INTERVAL_SECONDS
        assert plugin.timeout_seconds == C.DEFAULT_TIMEOUT_SECONDS
        assert plugin.dynamic_limit == C.DEFAULT_DYNAMIC_LIMIT
        assert plugin.uid_concurrency == C.DEFAULT_UID_CONCURRENCY
        assert plugin.uid_task_gap_seconds == C.DEFAULT_UID_TASK_GAP_SECONDS
        assert plugin.empty_retry_count == C.DEFAULT_EMPTY_RETRY_COUNT
        assert plugin.http_retry_count == C.DEFAULT_HTTP_RETRY_COUNT
        assert plugin.video_quality_label == C.DEFAULT_QUALITY_LABEL
        assert plugin.video_max_duration_seconds == C.DEFAULT_VIDEO_MAX_DURATION_SECONDS
        assert plugin.admin_ids == set()

    def test_retry_interval_negative_is_clamped(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"empty_retry_delay_seconds": -3, "http_retry_delay_seconds": -1}})
        assert plugin.empty_retry_delay_seconds == C.DEFAULT_EMPTY_RETRY_DELAY_SECONDS
        assert plugin.http_retry_delay_seconds == C.DEFAULT_HTTP_RETRY_DELAY_SECONDS

    def test_bool_coercion(self, plugin_factory):
        plugin = plugin_factory({"features": {"enable_bili_parse": 0}, "video_download": {"enable": 1}})
        assert plugin.enable_bili_parse is False
        assert plugin.enable_video_download is True


class TestDataDir:
    def test_uses_astrbot_root(self, plugin_factory, tmp_path):
        plugin = plugin_factory({})
        assert plugin.data_path == tmp_path / "data" / "plugin_data" / C.PLUGIN_NAME / "subscriptions.json"
        assert plugin.video_cache_dir.parent.parent == tmp_path / "data" / "plugin_data" / C.PLUGIN_NAME
        assert plugin.data_path.parent.exists()

    def test_fallback_when_star_tools_fails(self, monkeypatch, tmp_path):
        def boom(*_args, **_kwargs):
            raise RuntimeError("no root")

        monkeypatch.delenv("ASTRBOT_ROOT", raising=False)
        monkeypatch.setattr(M.StarTools, "get_data_dir", boom)
        plugin = M.BiliAllPlugin(context=None, config={})
        assert plugin.data_path == Path.cwd() / "data" / "plugin_data" / C.PLUGIN_NAME / "subscriptions.json"


class TestValidateConfig:
    def test_ok(self, plugin_factory):
        errors, warnings = plugin_factory({})._validate_config()
        assert errors == []
        assert any("empty_retry_count" in w for w in warnings) is False

    def test_warns_for_high_quality_without_cookie(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"enable": True, "quality": "1080P"}})
        errors, warnings = plugin._validate_config()
        assert errors == []
        assert any("1080P" in w for w in warnings)

    def test_no_warning_for_high_quality_with_cookie(self, plugin_factory):
        plugin = plugin_factory(
            {
                "video_download": {"enable": True, "quality": "1080P", "bili_cookie": "SESSDATA=x"},
            }
        )
        _errors, warnings = plugin._validate_config()
        assert not any("1080P" in w for w in warnings)

    def test_warns_for_bad_cookie_and_zero_retry(self, plugin_factory):
        plugin = plugin_factory(
            {
                "subscription": {"empty_retry_count": 0, "poll_interval_seconds": 30},
                "video_download": {"bili_cookie": "abc=1"},
            }
        )
        errors, warnings = plugin._validate_config()
        assert errors == []
        assert any("SESSDATA" in w for w in warnings)
        assert any("empty_retry_count" in w for w in warnings)
        assert any("60 秒" in w for w in warnings)

    def test_detects_out_of_range_values(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.video_quality_label = "NOPE"
        plugin.poll_interval_seconds = 0
        plugin.timeout_seconds = 0
        plugin.dynamic_limit = 0
        plugin.uid_concurrency = 0
        plugin.video_max_duration_seconds = -1
        plugin.video_max_size_mb = -1
        plugin.uid_task_gap_seconds = -1
        plugin.empty_retry_count = -1
        errors, _warnings = plugin._validate_config()
        joined = "\n".join(errors)
        assert "NOPE" in joined
        assert "poll_interval_seconds" in joined
        assert "timeout_seconds" in joined
        assert "dynamic_limit" in joined
        assert "uid_concurrency" in joined
        assert "max_duration_seconds" in joined
        assert "max_size_mb" in joined
        assert "uid_task_gap_seconds" in joined
        assert "empty_retry_count" in joined
        assert len(errors) == 9


class TestFmtHelpers:
    def test_status_icon(self):
        """状态标记必须是纯文本（插件输出不包含 emoji）。"""
        assert M._status_icon("ok") == "[正常]"
        assert M._status_icon("warn") == "[警告]"
        assert M._status_icon("fail") == "[异常]"
        assert M._status_icon("unknown") == "[异常]"

    def test_parse_uid_args(self):
        p = M.BiliAllPlugin._parse_uid_args(["/bili_sub", "123", "video", "抽奖"])
        assert (p.uid, p.filter_types, p.filter_regex, p.error) == (123, ["video"], ["抽奖"], "")
        p2 = M.BiliAllPlugin._parse_uid_args(["/x", "123"])
        assert (p2.uid, p2.filter_types, p2.filter_regex) == (123, [], [])
        assert M.BiliAllPlugin._parse_uid_args(["/x"]) is None
        assert M.BiliAllPlugin._parse_uid_args(["/x", "abc"]) is None
        assert M.BiliAllPlugin._parse_uid_args(["/x", ""]) is None
        assert M.BiliAllPlugin._parse_uid_args(["/x", "0"]) is None
        assert M.BiliAllPlugin._parse_uid_args(["/x", str(MAX_UID + 1)]) is None
        p3 = M.BiliAllPlugin._parse_uid_args(["/g", "sess", "456", "video"], uid_pos=2)
        assert (p3.uid, p3.filter_types) == (456, ["video"])

    def test_parse_uid_args_reports_invalid_regex(self):
        parsed = M.BiliAllPlugin._parse_uid_args(["/x", "123", "re:[unclosed"])
        assert parsed.uid == 123
        assert "正则非法" in parsed.error

    def test_parse_uid_args_reports_too_long_regex(self):
        parsed = M.BiliAllPlugin._parse_uid_args(["/x", "123", "re:" + "a" * (MAX_FILTER_REGEX_LEN + 1)])
        assert "正则过长" in parsed.error

    def test_fmt_filter(self):
        assert M.BiliAllPlugin._fmt_filter([], []) == ""
        assert M.BiliAllPlugin._fmt_filter(["video"], []) == "\n类型过滤: video"
        assert M.BiliAllPlugin._fmt_filter([], ["a"]) == "\n正则过滤: a"

    def test_fmt_filter_inline(self):
        assert M.BiliAllPlugin._fmt_filter_inline(Subscription(uid=1)) == "无"
        sub = Subscription(uid=1, filter_types=["video", "draw"], filter_regex=["抽奖"])
        assert M.BiliAllPlugin._fmt_filter_inline(sub) == "类型 video、draw、正则 抽奖"

    def test_fmt_sub_line(self):
        assert M.BiliAllPlugin._fmt_sub_line(1, Subscription(uid=2)) == "1) UID 2 ｜ 状态: 未开播 ｜ 过滤: 无"
        line = M.BiliAllPlugin._fmt_sub_line(2, Subscription(uid=3, is_live=True, filter_types=["video"]))
        assert line == "2) UID 3 ｜ 状态: 直播中 ｜ 过滤: 类型 video"


class TestQualityCandidates:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("4K", [120, 116, 112, 80, 64, 32, 16]),
            ("720P", [64, 32, 16]),
            ("360P", [16]),
            ("unknown", [64, 32, 16]),
        ],
    )
    def test_candidates(self, label, expected):
        assert M.BiliAllPlugin._quality_candidates(label) == expected


class TestHeaders:
    def test_variants(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"bili_cookie": "SESSDATA=x"}})
        assert plugin._headers()["User-Agent"]
        assert plugin._headers(referer=True)["Referer"] == "https://www.bilibili.com/"
        assert plugin._headers(referer_uid=5)["Referer"] == C.DYNAMIC_SPACE_URL.format(uid=5)
        assert plugin._headers(cookie=True)["Cookie"] == "SESSDATA=x"
        assert "Cookie" not in plugin._headers()
        assert "Referer" not in plugin._headers(cookie=True)

    def test_no_cookie_configured(self, plugin_factory):
        assert "Cookie" not in plugin_factory({})._headers(cookie=True)
