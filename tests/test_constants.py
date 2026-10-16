"""常量模块与发布资源一致性测试。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import bili_constants as C
import main as M

ROOT = Path(__file__).resolve().parent.parent


class TestRegexes:
    def test_bv_pattern(self):
        assert C.RE_BV.findall("看这个 BV1GJ411x7h7 视频") == ["BV1GJ411x7h7"]

    def test_av_pattern_case_insensitive(self):
        assert C.RE_AV.findall("av123 和 AV456") == ["av123", "AV456"]

    def test_short_link(self):
        assert C.RE_SHORT_LINK.findall("https://b23.tv/abcd123") == ["https://b23.tv/abcd123"]
        assert C.RE_SHORT_LINK.findall("https://bili2233.cn/xyz") == ["https://bili2233.cn/xyz"]

    def test_video_link(self):
        got = C.RE_VIDEO_LINK.findall("https://www.bilibili.com/video/BV1xx411c7mD?p=2")
        assert got == ["https://www.bilibili.com/video/BV1xx411c7mD?p=2"]

    def test_message_pattern_covers_all_forms(self):
        text = "BV1GJ411x7h7 av123 https://b23.tv/aaa https://m.bilibili.com/video/BV2"
        assert C.RE_MESSAGE.search(text)
        for token in ("BV1GJ411x7h7", "av123", "https://b23.tv/aaa"):
            assert token in text

    def test_url_pattern(self):
        assert C.RE_URL.findall("a https://x.com/1 b http://y.cn/2") == [
            "https://x.com/1",
            "http://y.cn/2",
        ]

    def test_lottery_pattern(self):
        assert C.RE_LOTTERY.search("恭喜 张三 等3位同学中奖")
        assert not C.RE_LOTTERY.search("普通的动态内容")


class TestTables:
    def test_quality_table_sorted(self):
        values = list(C.QUALITY_TO_QN.values())
        assert values == sorted(values)
        assert C.QUALITY_TO_QN[C.DEFAULT_QUALITY_LABEL] == C.DEFAULT_QN

    def test_filter_types(self):
        assert {
            "forward",
            "lottery",
            "video",
            "article",
            "draw",
            "live",
            "forward_lottery",
        } == C.VALID_FILTER_TYPES

    def test_dynamic_type_labels(self):
        assert C.DYNAMIC_TYPE_LABELS["DYNAMIC_TYPE_AV"] == "视频动态"
        assert C.DEFAULT_DYNAMIC_TYPE_LABEL == "动态"

    def test_http_silent_codes(self):
        assert 412 in C.DYNAMIC_SILENT_CODES
        assert {500, 502, 503, 504, 408, 425, 429} <= C.RETRYABLE_HTTP


@pytest.fixture(scope="module")
def metadata() -> dict:
    return yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))


class TestMetadataFile:
    def test_required_fields(self, metadata):
        for field in ("name", "desc", "version", "author"):
            assert isinstance(metadata.get(field), str)
            assert metadata[field].strip()

    def test_name_matches_constant(self, metadata):
        assert metadata["name"] == C.PLUGIN_NAME
        assert metadata["name"].startswith("astrbot_plugin_")

    def test_version_matches_constant(self, metadata):
        assert metadata["version"].lstrip("v") == C.PLUGIN_VERSION

    def test_repo_and_display(self, metadata):
        assert metadata["repo"] == C.PLUGIN_REPO
        assert metadata["display_name"] == C.PLUGIN_DISPLAY_NAME
        assert metadata["support_platforms"] == ["aiocqhttp"]

    def test_astrbot_version_is_pep440(self, metadata):
        from packaging.specifiers import SpecifierSet

        SpecifierSet(metadata["astrbot_version"])

    def test_short_desc_present(self, metadata):
        assert metadata["short_desc"].strip()


class TestConfSchema:
    def test_supported_types(self, schema):
        valid = {
            "int",
            "float",
            "bool",
            "string",
            "text",
            "list",
            "file",
            "object",
            "template_list",
            "dict",
        }
        for section in schema.values():
            assert section["type"] == "object"
            for item in section["items"].values():
                assert item["type"] in valid
                assert "default" in item
                assert item["description"]

    def test_defaults_match_runtime(self, schema, plugin_factory):
        plugin = plugin_factory({})
        items = schema["subscription"]["items"]
        assert plugin.poll_interval_seconds == items["poll_interval_seconds"]["default"]
        assert plugin.timeout_seconds == items["timeout_seconds"]["default"]
        assert plugin.dynamic_limit == items["dynamic_limit"]["default"]
        assert plugin.uid_concurrency == items["uid_concurrency"]["default"]
        assert plugin.uid_task_gap_seconds == items["uid_task_gap_seconds"]["default"]
        assert plugin.recent_ids_limit == items["recent_ids_limit"]["default"]
        assert plugin.empty_retry_count == items["empty_retry_count"]["default"]
        assert plugin.empty_retry_delay_seconds == items["empty_retry_delay_seconds"]["default"]
        assert plugin.http_retry_count == items["http_retry_count"]["default"]
        assert plugin.http_retry_delay_seconds == items["http_retry_delay_seconds"]["default"]
        # plugin_factory 会为了测试确定性把节流置 0，这里用未覆盖的实例校验默认值。
        assert (
            M.BiliAllPlugin(context=plugin.context, config={}).min_request_interval_seconds
            == items["min_request_interval_seconds"]["default"]
        )
        download = schema["video_download"]["items"]
        assert plugin.video_quality_label == download["quality"]["default"]
        assert plugin.video_max_duration_seconds == download["max_duration_seconds"]["default"]
        assert plugin.video_max_size_mb == download["max_size_mb"]["default"]
        assert plugin.video_retry_count == download["retry_count"]["default"]
        assert plugin.enable_video_download == download["enable"]["default"]
        assert plugin.video_auto_delete == download["auto_delete"]["default"]
        features = schema["features"]["items"]
        assert plugin.enable_bili_parse == features["enable_bili_parse"]["default"]
        assert plugin.send_video_info == features["send_video_info"]["default"]
        assert plugin.enable_llm_tool == features["enable_llm_tool"]["default"]

    def test_loads_through_astrbot_config(self, schema, tmp_path):
        """使用 AstrBot 自身的配置解析器验证 schema 合法。"""
        from astrbot.api import AstrBotConfig

        config = AstrBotConfig(config_path=str(tmp_path / "conf.json"), schema=schema)
        assert config["subscription"]["poll_interval_seconds"] == 300
        assert config["features"]["enable_bili_parse"] is True
        assert config["video_download"]["quality"] == "720P"

    def test_bounds_are_sane(self, schema):
        for section in schema.values():
            for key, item in section["items"].items():
                if "min" in item and "max" in item:
                    assert item["min"] <= item["default"] <= item["max"], key


class TestRepoAssets:
    def test_requirements(self):
        content = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        assert "aiohttp" in content

    def test_readme_mentions_commands_and_badge(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in (
            "/bili_sub ",
            "/bili_sub_del",
            "/bili_sub_list",
            "/bili_sub_test",
            "/bili_state",
            "/bili_global_sub",
            "/bili_global_del",
            "/bili_global_list",
        ):
            assert command in readme
        assert "coverage" in readme.lower()

    def test_license_present(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "GNU" in text or "MIT" in text

    def test_no_register_decorator(self):
        """4.x 起 @register 已废弃，插件应依赖自动发现 + metadata.yaml。"""
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        assert not re.search(r"^\s*@register\(", source, re.MULTILINE)

    def test_scripts_are_syntax_valid(self):
        for path in (ROOT / "scripts").glob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
