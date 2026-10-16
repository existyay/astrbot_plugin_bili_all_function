"""补充边界分支测试（覆盖率收尾）。"""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest
from conftest import (
    FIXTURE_DIR,
    FakeEvent,
    FakeResponse,
    chain_text,
    collect,
    json_response,
    load_fixture,
    route,
)

import bili_helpers as H
import bili_logic as L
import main as M
from bili_models import Subscription

FEED = load_fixture("dynamic_feed_jiaran.json")
UID = FEED["_fixture"]["uid"]
LIVE = load_fixture("live_status.json")


# ───────────────────────── bili_helpers 分支 ─────────────────────────


class TestFormatDurationBranches:
    @pytest.mark.parametrize("value", [float("nan"), object(), [], {}])
    def test_unconvertible_values(self, value):
        assert H.format_duration(value) in {"0:00", "1:00"}


class TestAuthorBranches:
    def test_blank_name_falls_back(self):
        assert H.dynamic_author_name({"modules": {"module_author": {"name": "   "}}}) == "UP主"

    def test_non_dict_author_falls_back(self):
        assert H.dynamic_author_name({"modules": {"module_author": "bad"}}) == "UP主"

    @pytest.mark.parametrize("helper", ["dynamic_author_uid", "dynamic_pub_ts"])
    def test_non_dict_author(self, helper):
        item = {"modules": {"module_author": "bad"}}
        assert getattr(H, helper)(item) == ("" if helper == "dynamic_author_uid" else 0)


class TestForwardLotteryBranches:
    def test_nodes_without_lottery_text_but_lottery_type(self):
        item = {
            "orig": {
                "modules": {
                    "module_dynamic": {
                        "type": "DYNAMIC_TYPE_LOTTERY",
                        "major": {"opus": {"summary": {"rich_text_nodes": [{"text": "其他"}]}}},
                    },
                },
            },
        }
        assert H.is_forward_lottery(item) is True


class TestNormalizeModulesBranches:
    def test_non_dict_module_values_are_skipped(self):
        item = {
            "modules": [
                {"module_type": "MODULE_TYPE_X", "module_x": "not-a-dict"},
                {"module_type": "MODULE_TYPE_AUTHOR", "module_author": {"name": "UP"}},
            ],
        }
        H.normalize_modules(item)
        assert item["modules"] == {"module_author": {"name": "UP"}}

    def test_non_dict_module_entry_is_skipped(self):
        item = {"modules": ["bad", {"module_type": "MODULE_TYPE_AUTHOR", "module_author": {"name": "UP"}}]}
        H.normalize_modules(item)
        assert item["modules"] == {"module_author": {"name": "UP"}}

    def test_dyn_forward_item_not_dict(self):
        item = {"modules": {"module_dynamic": {"dyn_forward": {"item": "bad"}}}}
        H.normalize_modules(item)
        assert "orig" not in item


# ───────────────────────── bili_logic 分支 ─────────────────────────


class TestDynamicBodyBranches:
    def _av(self, archive: dict) -> dict:
        return {
            "id_str": "1",
            "type": "DYNAMIC_TYPE_AV",
            "modules": {"module_dynamic": {"major": {"archive": archive}}},
        }

    def test_archive_without_play_count(self):
        text = chain_text(L.build_dynamic_chain(self._av({"bvid": "BV1", "stat": {"danmaku": 3}})))
        assert "弹幕 3" in text and "播放" not in text

    def test_archive_without_any_meta(self):
        text = chain_text(L.build_dynamic_chain(self._av({"bvid": "BV1"})))
        assert text.startswith("[B站动态] UP主 · 视频动态")

    def test_archive_with_zero_duration(self):
        text = chain_text(L.build_dynamic_chain(self._av({"bvid": "BV1", "duration_text": "", "duration": 0})))
        assert "时长" not in text

    def test_forward_with_orig_but_no_text(self):
        item = {
            "id_str": "2",
            "type": "DYNAMIC_TYPE_FORWARD",
            "modules": {"module_dynamic": {"desc": {"text": "转发语"}}},
            "orig": {"id_str": "9", "modules": {}},
        }
        text = chain_text(L.build_dynamic_chain(item))
        assert "转发语" in text
        assert "@" not in text


# ───────────────────────── main.py 分支 ─────────────────────────


class TestRequestReadError:
    async def test_body_read_error_is_caught(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"http_retry_delay_seconds": 0, "http_retry_count": 0}},
            handler=route(
                (
                    "api",
                    lambda *_: FakeResponse(
                        content_error=aiohttp.ClientConnectionError("dropped"),
                    ),
                )
            ),
        )
        assert await plugin._request(url="https://api/x", headers={}, scene="t") is None


class TestLoadSubscriptionBranches:
    def test_session_with_only_invalid_entries_is_dropped(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.data_path.parent.mkdir(parents=True, exist_ok=True)
        plugin.data_path.write_text(
            json.dumps({"bad": [{"no_uid": 1}], "good": [{"uid": 3}]}),
            encoding="utf-8",
        )
        plugin._load_subscriptions()
        assert set(plugin.subscriptions) == {"good"}


class TestExtractLinksBranches:
    def test_nested_list_of_dicts(self, plugin_factory):
        plugin = plugin_factory({})
        payload = {"a": ["https://b23.tv/1", {"url": "https://b23.tv/2"}]}
        assert plugin._extract_links_from_json(payload) == ["https://b23.tv/1", "https://b23.tv/2"]

    def test_meta_not_dict(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._extract_links_from_json({"meta": "bad", "url": "https://b23.tv/x"}) == ["https://b23.tv/x"]

    def test_dict_value_then_more_keys(self, plugin_factory):
        plugin = plugin_factory({})
        payload = {"nested": {"url": "https://b23.tv/a"}, "tail": "https://b23.tv/b"}
        assert plugin._extract_links_from_json(payload) == ["https://b23.tv/a", "https://b23.tv/b"]

    def test_scalar_values_are_ignored(self, plugin_factory):
        plugin = plugin_factory({})
        payload = {"num": 5, "flag": True, "list": [7, {"url": "https://b23.tv/c"}], "url": "https://b23.tv/d"}
        assert plugin._extract_links_from_json(payload) == ["https://b23.tv/c", "https://b23.tv/d"]


class TestPlayurlBranches:
    async def test_dash_video_without_url(self, plugin_factory):
        payload = {"code": 0, "data": {"dash": {"video": [{"baseUrl": "  ", "id": 64}]}}}
        plugin = plugin_factory({}, handler=route(("playurl", lambda *_: json_response(payload))))
        assert await plugin._fetch_playurl(bvid="BV1", cid=1, qn=64) is None


class TestLiveStatusBranches:
    async def test_data_not_dict_falls_back(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(
                ("get_status_info_by_uids", lambda *_: json_response({"code": 0, "data": "bad"})),
                ("getRoomInfoOld", lambda *_: json_response({"code": 0, "data": {"uname": "UP"}})),
            ),
        )
        row = await plugin._fetch_live_status(UID)
        assert row and row["uname"] == "UP"


class TestPollGap:
    async def test_uid_task_gap_is_applied(self, plugin_factory, monkeypatch):
        responder = lambda *_: json_response(FEED)  # noqa: E731
        plugin = plugin_factory(
            {"subscription": {"uid_task_gap_seconds": 1}},
            handler=route(("desktop/v1/feed", responder), ("live", lambda *_: json_response({"code": -1}))),
        )
        assert plugin.uid_task_gap_seconds == 1
        sleeps: list[float] = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(M.asyncio, "sleep", fake_sleep)
        sub = Subscription(uid=UID)
        assert await plugin._poll_uid(UID, [("s1", sub)], asyncio.Semaphore(1)) is True
        assert sleeps == [1]


class TestCheckSubscriptionBranches:
    async def test_skips_empty_sessions(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}},
            handler=route(
                ("desktop/v1/feed", lambda *_: json_response(FEED)),
                ("get_status_info_by_uids", lambda *_: json_response(LIVE)),
            ),
        )
        plugin.subscriptions = {"empty": [], "full": [Subscription(uid=UID)]}
        status, msg = await plugin._check_subscription("other")
        assert status == "warn"
        assert str(UID) in msg


class TestBiliStateBranches:
    async def test_errors_without_warnings(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        monkeypatch.setattr(plugin, "_refresh_config", lambda: None)
        plugin.video_quality_label = "NOPE"  # 唯一错误项，且不触发任何警告
        plugin.empty_retry_count = 2
        plugin.poll_interval_seconds = 300
        plugin.enable_video_download = False
        plugin.bili_cookie = ""
        plugin.min_request_interval_seconds = 0.2
        plugin.uid_concurrency = 3
        plugin.recent_ids_limit = 20
        texts = [
            chain_text([c for c in r.chain if hasattr(c, "text")])
            for r in await collect(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        ]
        assert "配置检查未通过" in texts[0]
        assert "配置警告" not in texts[0]


class TestFixtureIntegrity:
    def test_all_fixtures_are_valid_json(self):
        for path in FIXTURE_DIR.glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))

    def test_feed_fixtures_keep_raw_module_list(self):
        """夹具必须保留新版接口的 modules 数组结构，否则规范化逻辑将失去覆盖。"""
        for path in FIXTURE_DIR.glob("dynamic_feed_*.json"):
            items = json.loads(path.read_text(encoding="utf-8"))["data"]["items"]
            assert isinstance(items[0]["modules"], list)
