"""安全与输入校验工具测试（脱敏、域名白名单、正则护栏、文件名清理）。"""

from __future__ import annotations

import re

import pytest

import bili_constants as C
import bili_helpers as H


class TestMaskSecret:
    def test_empty_values(self):
        assert H.mask_secret(None) == ""
        assert H.mask_secret("") == ""
        assert H.mask_secret("   ") == ""

    def test_masks_long_values(self):
        masked = H.mask_secret("SESSDATA=abcdef123456; bili_jct=0123456789abcdef")
        assert "abcdef" not in masked.replace("abc***", "")
        assert masked.startswith("SESSDATA=abcd***")
        assert "bili_jct=0123***" in masked

    def test_short_value_is_fully_hidden(self):
        assert H.mask_secret("token=abc") == "token=***"

    def test_segment_without_value(self):
        assert H.mask_secret("rawtoken") == "***"

    def test_newline_separated_cookie(self):
        masked = H.mask_secret("SESSDATA=abcdefgh\nbuvid3=XYZ123456")
        assert masked == "SESSDATA=abcd***; buvid3=XYZ1***"

    def test_blank_segments_are_skipped(self):
        assert H.mask_secret("a=123456;;  ;b=234567") == "a=1234***; b=2345***"


class TestSensitiveDetection:
    def test_has_sensitive_cookie_text(self):
        assert H.has_sensitive_cookie_text("cookie: SESSDATA=1")
        assert not H.has_sensitive_cookie_text("nothing here")


class TestSanitizeCookie:
    def test_strips_crlf(self):
        assert H.sanitize_cookie("a=1\r\nX-Injected: 1\n") == "a=1X-Injected: 1"

    def test_strips_nul_and_spaces(self):
        assert H.sanitize_cookie("  a=1\x00  ") == "a=1"

    def test_none(self):
        assert H.sanitize_cookie(None) == ""


class TestHostTrust:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://api.bilibili.com/x/web-interface/nav", "api.bilibili.com"),
            ("https://www.bilibili.com/video/BV1", "www.bilibili.com"),
            ("https://b23.tv/abc", "b23.tv"),
            ("not a url", ""),
            ("", ""),
        ],
    )
    def test_host_of(self, url, expected):
        assert H.host_of(url) == expected

    def test_host_of_invalid_type(self):
        assert H.host_of(123) == ""

    def test_host_of_swallows_str_errors(self):
        class BadUrl:
            def __str__(self):
                raise TypeError("no string form")

        assert H.host_of(BadUrl()) == ""

    def test_trusted_hosts(self):
        assert H.is_trusted_host("https://api.bilibili.com/x")
        assert H.is_trusted_host("https://upos-sz-mirror.bilivideo.com/v.mp4")
        assert not H.is_trusted_host("https://evil-bilibili.com/x")
        assert not H.is_trusted_host("https://bilibili.com.evil.com/x")
        assert not H.is_trusted_host("")
        assert not H.is_trusted_host(None)

    def test_allowlist_is_configurable(self):
        assert H.is_trusted_host("https://example.com/a", ("example.com",))
        assert not H.is_trusted_host("https://other.com/a", ("example.com",))


class TestUidValidation:
    def test_valid(self):
        assert H.is_valid_uid("123")
        assert H.is_valid_uid(4_000_000_000)

    @pytest.mark.parametrize("value", ["", "abc", "0", "-1", " 12a", None, str(C.MAX_UID + 1)])
    def test_invalid(self, value):
        assert not H.is_valid_uid(value)


class TestSanitizeFilename:
    def test_removes_path_separators(self):
        assert H.sanitize_filename("../../etc/passwd") == "etc_passwd"
        assert H.sanitize_filename("a\\b:c*d?.mp4") == "a_b_c_d_.mp4"

    def test_empty_uses_fallback(self):
        assert H.sanitize_filename("", fallback="video") == "video"
        assert H.sanitize_filename("...", fallback="video") == "video"

    def test_truncates(self):
        assert len(H.sanitize_filename("B" * 200)) == 80


class TestFormatBytes:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [(0, "0B"), (512, "512B"), (2048, "2.0KB"), (5 * 1024 * 1024, "5.0MB"), (3 * 1024**3, "3.0GB")],
    )
    def test_formats(self, size, expected):
        assert H.format_bytes(size) == expected

    def test_invalid_input(self):
        assert H.format_bytes(None) == "0B"
        assert H.format_bytes("abc") == "0B"

    def test_extreme_values_fall_through_to_gb(self):
        assert H.format_bytes(float("inf")) == "infGB"


class TestFilterPatternGuards:
    def test_compile_dedupes_and_ignores_blank(self):
        compiled = H.compile_filter_patterns(["抽奖", "抽奖", "", "  ", "video"])
        assert [p.pattern for p in compiled] == ["抽奖", "video"]
        assert all(isinstance(p, re.Pattern) for p in compiled)

    def test_compile_skips_invalid_and_too_long(self):
        compiled = H.compile_filter_patterns(["[unclosed", "a" * (C.MAX_FILTER_REGEX_LEN + 1), "ok"])
        assert [p.pattern for p in compiled] == ["ok"]

    def test_compile_caps_count(self):
        patterns = [f"p{i}" for i in range(C.MAX_FILTER_REGEX_COUNT + 5)]
        assert len(H.compile_filter_patterns(patterns)) == C.MAX_FILTER_REGEX_COUNT

    def test_compile_accepts_precompiled_patterns(self):
        compiled = H.compile_filter_patterns([re.compile("abc"), re.compile("abc")])
        assert len(compiled) == 1

    def test_compile_handles_none(self):
        assert H.compile_filter_patterns(None) == []

    def test_validate_ok(self):
        assert H.validate_filter_patterns(["抽奖", "视频"]) == ""

    def test_validate_rejects_invalid(self):
        assert "正则非法" in H.validate_filter_patterns(["[unclosed"])

    def test_validate_rejects_too_long(self):
        assert "正则过长" in H.validate_filter_patterns(["a" * (C.MAX_FILTER_REGEX_LEN + 1)])

    def test_validate_rejects_too_many(self):
        many = [f"p{i}" for i in range(C.MAX_FILTER_REGEX_COUNT + 1)]
        assert "数量过多" in H.validate_filter_patterns(many)

    def test_validate_ignores_blank_and_duplicates(self):
        assert H.validate_filter_patterns(["", "抽奖", "抽奖"]) == ""


class TestMatchesAnyPattern:
    def test_empty_inputs(self):
        assert H.matches_any_pattern("", [re.compile("a")]) is False
        assert H.matches_any_pattern("abc", []) is False

    def test_matches_case_insensitively_for_strings(self):
        assert H.matches_any_pattern("BILIBILI", ["bilibili"]) is True

    def test_matches_precompiled(self):
        assert H.matches_any_pattern("抽奖活动", [re.compile("抽奖")]) is True

    def test_non_matching_pattern_falls_through_to_next(self):
        assert (
            H.matches_any_pattern(
                "bcd",
                [re.compile("zzz"), re.compile("bcd")],
            )
            is True
        )

    def test_invalid_string_pattern_is_skipped(self):
        assert H.matches_any_pattern("abc", ["[unclosed", "abc"]) is True
        assert H.matches_any_pattern("abc", ["[unclosed"]) is False

    def test_bad_pattern_type_is_skipped(self):
        assert H.matches_any_pattern("abc", [123]) is False

    def test_pattern_raising_is_skipped_and_next_still_runs(self):
        class Exploding:
            def search(self, _text):
                raise TypeError("boom")

        assert H.matches_any_pattern("abc", [Exploding(), re.compile("abc")]) is True
        assert H.matches_any_pattern("zzz", [Exploding()]) is False
