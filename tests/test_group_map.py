"""群屏蔽列表与「设置面板订阅映射表」测试。"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pytest
from conftest import FakeEvent, collect, json_response, load_fixture, route

import bili_constants as C
import bili_helpers as H
import main as M
from bili_models import GroupBinding, Subscription

FEED = load_fixture("dynamic_feed_jiaran.json")
UID = FEED["_fixture"]["uid"]
GROUP = "aiocqhttp:GroupMessage:12345"


def feed_handler():
    """动态 + 直播接口的固定响应（供映射应用测试使用）。"""
    return route(
        ("desktop/v1/feed", lambda *_: json_response(FEED)),
        ("get_status_info_by_uids", lambda *_: json_response({"code": 0, "data": {}})),
        default=lambda *_: json_response({"code": -1, "message": "no"}),
    )


class RecordingConfig(OrderedDict):
    """带 ``save_config`` 的配置对象，用于断言回写确实落盘。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.saved = 0
        self.fail = False

    def save_config(self, *_args: Any, **_kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("disk full")
        self.saved += 1


# ───────────────────────── 纯函数：会话规范化与屏蔽列表 ─────────────────────────


class TestNormalizeSession:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("123456", f"{C.GROUP_SESSION_PREFIX}123456"),
            ("GroupMessage:123456", f"{C.GROUP_SESSION_PREFIX}123456"),
            ("FriendMessage:9", "aiocqhttp:FriendMessage:9"),
            ("aiocqhttp:GroupMessage:1", "aiocqhttp:GroupMessage:1"),
            ("webchat:FriendMessage:x", "webchat:FriendMessage:x"),
            ("*", "*"),
            ("", ""),
            (None, ""),
            ("target-session", "target-session"),
            ("  42  ", f"{C.GROUP_SESSION_PREFIX}42"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert H.normalize_session(raw) == expected


class TestParseBlockedGroups:
    def test_multiple_separators_and_comments(self):
        text = "123\n# 备注\n456, 789；# 行内注释\n aiocqhttp:GroupMessage:7 "
        assert H.parse_blocked_groups(text) == [
            f"{C.GROUP_SESSION_PREFIX}123",
            f"{C.GROUP_SESSION_PREFIX}456",
            f"{C.GROUP_SESSION_PREFIX}789",
            "aiocqhttp:GroupMessage:7",
        ]

    def test_dedupes_and_accepts_sequences(self):
        assert H.parse_blocked_groups(["1", "1", "GroupMessage:1"]) == [f"{C.GROUP_SESSION_PREFIX}1"]

    def test_empty(self):
        assert H.parse_blocked_groups("") == []
        assert H.parse_blocked_groups(None) == []

    def test_caps_entries(self, monkeypatch):
        monkeypatch.setattr(H, "MAX_BLOCKED_GROUPS", 3)
        assert len(H.parse_blocked_groups("1 2 3 4 5")) == 3


class TestIsBlockedSession:
    @pytest.mark.parametrize(
        "pattern",
        [GROUP, "aiocqhttp:GroupMessage:12345", "GroupMessage:12345", "12345"],
    )
    def test_matches_full_and_suffix(self, pattern):
        assert H.is_blocked_session(GROUP, [pattern]) is True

    def test_no_patterns(self):
        assert H.is_blocked_session(GROUP, []) is False

    def test_other_session(self):
        assert H.is_blocked_session(GROUP, ["aiocqhttp:GroupMessage:999"]) is False

    def test_empty_session(self):
        assert H.is_blocked_session("", ["*"]) is False
        assert H.is_blocked_session(None, ["*"]) is False

    def test_wildcards(self):
        assert H.is_blocked_session(GROUP, ["*"]) is True
        assert H.is_blocked_session(GROUP, ["aiocqhttp:GroupMessage:*"]) is True
        assert H.is_blocked_session(GROUP, ["aiocqhttp:FriendMessage:*"]) is False

    def test_blank_pattern_ignored(self):
        assert H.is_blocked_session(GROUP, ["", "   "]) is False


# ───────────────────────── 纯函数：映射表解析 ─────────────────────────


class TestParseGroupMap:
    def test_basic_mapping(self):
        bindings = H.parse_group_map("123456 = 946974\n")
        assert bindings == [GroupBinding(session=f"{C.GROUP_SESSION_PREFIX}123456", uid=946974)]

    def test_filters_and_multiple_uids(self):
        text = "946974 = 672328094 forward, 2 (^标题 \\d+$; lottery)"
        bindings = H.parse_group_map(text)
        assert [(b.uid, b.filter_types) for b in bindings] == [(672328094, ["forward"]), (2, ["lottery"])]
        assert bindings[1].filter_regex == ["^标题", "\\d+$"]

    def test_inline_filter_group(self):
        bindings = H.parse_group_map("946974 = 2(forward ^标题$)")
        assert bindings[0].filter_types == ["forward"]
        assert bindings[0].filter_regex == ["^标题$"]

    def test_backtick_filters_with_spaces_and_commas(self):
        text = "946974 = 2 `^标题 .*$, 后缀` forward"
        bindings = H.parse_group_map(text)
        assert bindings[0].filter_regex == ["^标题 .*$, 后缀"]
        assert bindings[0].filter_types == ["forward"]

    def test_tokenizer_edge_cases(self):
        # 连续空格（缓冲为空时命中空白）与行尾空的反引号包裹
        bindings = H.parse_group_map("946974 = 2  forward ``")
        assert [(b.uid, b.filter_types) for b in bindings] == [(2, ["forward"])]

    def test_whitespace_separator_and_full_umo(self):
        bindings = H.parse_group_map("aiocqhttp:GroupMessage:5 946974 video")
        assert bindings[0].session == "aiocqhttp:GroupMessage:5"
        assert bindings[0].filter_types == ["video"]

    def test_arrow_separators(self):
        assert H.parse_group_map("1 -> 2\n1 => 3\n1 → 4") == [
            GroupBinding(session=f"{C.GROUP_SESSION_PREFIX}1", uid=2),
            GroupBinding(session=f"{C.GROUP_SESSION_PREFIX}1", uid=3),
            GroupBinding(session=f"{C.GROUP_SESSION_PREFIX}1", uid=4),
        ]

    def test_comments_and_junk_lines(self):
        assert H.parse_group_map("# 注释\n\nhello\n1 =\n= 2\n") == []
        assert H.parse_group_map(None) == []

    def test_dedupes_same_pair(self):
        assert len(H.parse_group_map("1 = 2\n1 = 2")) == 1

    def test_skips_invalid_uid(self):
        assert H.parse_group_map("1 = abc") == []

    def test_long_text_is_truncated(self):
        text = "1 = 2\n" * 20000
        bindings = H.parse_group_map(text)
        assert 0 < len(bindings) <= C.MAX_GROUP_MAP_ENTRIES

    def test_caps_entries(self, monkeypatch):
        monkeypatch.setattr(H, "MAX_GROUP_MAP_ENTRIES", 2)
        assert len(H.parse_group_map("1 = 2\n1 = 3\n1 = 4")) == 2


class TestValidateGroupMap:
    def test_valid(self):
        assert H.validate_group_map("946974 = 2 forward") == []

    def test_reports_missing_separator(self):
        errors = H.validate_group_map("946974")
        assert errors and "分隔符" in errors[0]

    def test_reports_invalid_subscription(self):
        errors = H.validate_group_map("946974 = abc")
        assert errors and "UID 需为数字" in errors[0]

    def test_reports_invalid_regex(self):
        errors = H.validate_group_map("946974 = 2 re:[unclosed")
        assert any("过滤器无效" in e for e in errors)

    def test_reports_too_long(self, monkeypatch):
        monkeypatch.setattr(H, "MAX_GROUP_MAP_LEN", 10)
        errors = H.validate_group_map("1 = 2\n" * 10)
        assert any("过长" in e for e in errors)

    def test_reports_entry_overflow(self, monkeypatch):
        monkeypatch.setattr(H, "MAX_GROUP_MAP_ENTRIES", 1)
        errors = H.validate_group_map("1 = 2\n1 = 3")
        assert any("超过上限" in e for e in errors)

    def test_ignores_comments(self):
        assert H.validate_group_map("# a\n\n   ") == []


class TestRenderGroupMap:
    def test_round_trip_with_tricky_filters(self):
        bindings = [
            GroupBinding(
                session="aiocqhttp:GroupMessage:1",
                uid=2,
                filter_types=["forward"],
                filter_regex=["^标题 .*$, 后缀", "a`b"],
            )
        ]
        text = H.render_group_map("", bindings)
        assert C.GROUP_MAP_AUTO_HEADER in text
        assert H.parse_group_map(text) == bindings
        assert H.validate_group_map(text) == []

    def test_keeps_admin_section(self):
        text = H.render_group_map("# 管理员手写\n1 = 2", [GroupBinding(session="s", uid=3)])
        assert text.startswith("# 管理员手写\n1 = 2\n")
        assert H.parse_group_map(text) == [
            GroupBinding(session=f"{C.GROUP_SESSION_PREFIX}1", uid=2),
            GroupBinding(session="s", uid=3),
        ]

    def test_empty_bindings_clears_auto_section(self):
        assert H.render_group_map("# 手写\n", []) == "# 手写\n"
        assert H.render_group_map("", []) == ""

    def test_split_group_map_variants(self):
        text = f"# 手写\n{C.GROUP_MAP_AUTO_HEADER}\n1 = 2\n"
        assert H.split_group_map(text)[0] == "# 手写\n"
        assert H.split_group_map("# auto-sync 区\n1 = 2")[0] == ""
        assert H.split_group_map("1 = 2") == ("1 = 2", "")

    def test_format_line_without_filters(self):
        assert H.format_group_map_line("s", 2) == "s = 2"


# ───────────────────────── 插件：群屏蔽 ─────────────────────────


class TestBlockedGroupsPlugin:
    def test_config_parsed(self, plugin_factory):
        plugin = plugin_factory({"permission": {"blocked_groups": "12345\n# 注释\n999"}})
        assert plugin.blocked_groups == (f"{C.GROUP_SESSION_PREFIX}12345", f"{C.GROUP_SESSION_PREFIX}999")
        assert plugin._is_blocked(GROUP) is True
        assert plugin._is_blocked("aiocqhttp:GroupMessage:888") is False

    async def test_commands_are_silent_in_blocked_group(self, plugin_factory):
        plugin = plugin_factory({"permission": {"blocked_groups": "12345"}})
        for coro in (
            plugin.cmd_bili_sub(FakeEvent("/bili_sub 2")),
            plugin.cmd_bili_sub_del(FakeEvent("/bili_sub_del 2")),
            plugin.cmd_bili_sub_list(FakeEvent("/bili_sub_list")),
            plugin.cmd_bili_sub_test(FakeEvent("/bili_sub_test 2")),
            plugin.cmd_bili_state(FakeEvent("/bili_state")),
            plugin.cmd_bili_map_sync(FakeEvent("/bili_map_sync", admin=True)),
            plugin.cmd_bili_global_list(FakeEvent("/bili_global_list", admin=True)),
            plugin.cmd_bili_global_del(FakeEvent("/bili_global_del 12345 2", admin=True)),
        ):
            assert await collect(coro) == []
        assert plugin.subscriptions == {}

    async def test_parse_skipped_in_blocked_group(self, plugin_factory):
        plugin = plugin_factory({"permission": {"blocked_groups": "12345"}}, handler=feed_handler())
        event = FakeEvent("看看 https://www.bilibili.com/video/BV1Hw4m1r7dp")
        await plugin.handle_bili_parse(event)
        assert event.stopped is False
        assert plugin.context.sent == []

    async def test_parse_still_works_in_other_group(self, plugin_factory):
        plugin = plugin_factory(
            {"permission": {"blocked_groups": "12345"}},
            handler=route(
                ("web-interface/view", lambda *_: json_response({"code": 0, "data": {"bvid": "BV1Hw4m1r7dp"}})),
                default=lambda *_: json_response({"code": -1}),
            ),
        )
        event = FakeEvent("看看 https://www.bilibili.com/video/BV1Hw4m1r7dp", umo="aiocqhttp:GroupMessage:777")
        await plugin.handle_bili_parse(event)
        assert event.stopped is True

    async def test_llm_tool_reports_blocked(self, plugin_factory):
        plugin = plugin_factory({"permission": {"blocked_groups": "12345"}})
        text = await plugin.llm_bili_video_info(FakeEvent("BV1Hw4m1r7dp"), "BV1Hw4m1r7dp")
        assert "停用" in text

    async def test_safe_send_skips_blocked(self, plugin_factory):
        plugin = plugin_factory({"permission": {"blocked_groups": "12345"}})
        assert await plugin._safe_send(GROUP, [], "test") is False
        assert plugin.context.sent == []

    def test_active_subscriptions_filters_blocked(self, plugin_factory):
        plugin = plugin_factory({"permission": {"blocked_groups": "12345"}})
        plugin.subscriptions = {GROUP: [Subscription(uid=1)], "s2": [Subscription(uid=2)]}
        assert plugin._active_subscriptions() == {"s2": plugin.subscriptions["s2"]}
        plugin.blocked_groups = ()
        assert set(plugin._active_subscriptions()) == {GROUP, "s2"}

    async def test_poll_skips_blocked_session(self, plugin_factory):
        plugin = plugin_factory(
            {"permission": {"blocked_groups": "12345"}},
            handler=feed_handler(),
        )
        plugin.uid_task_gap_seconds = 0
        plugin._upsert_subscription(GROUP, UID, [], [], "")
        assert await plugin._poll_once() is False
        assert plugin.context.sent == []

    async def test_poll_still_pushes_to_other_session(self, plugin_factory):
        plugin = plugin_factory({"permission": {"blocked_groups": "12345"}}, handler=feed_handler())
        plugin.uid_task_gap_seconds = 0
        plugin._upsert_subscription("aiocqhttp:GroupMessage:777", UID, [], [], "")
        assert await plugin._poll_once() is True
        assert plugin.context.sent


# ───────────────────────── 插件：设置里的订阅映射 ─────────────────────────


class TestGroupMapApply:
    async def test_applies_new_bindings(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"group_subscriptions": "12345 = 946974, 672328094 forward"}},
            handler=feed_handler(),
        )
        added, updated = await plugin._apply_group_map()
        assert (added, updated) == (2, 0)
        assert [s.uid for s in plugin.subscriptions[GROUP]] == [946974, 672328094]
        assert plugin.subscriptions[GROUP][1].filter_types == ["forward"]
        # 锚点已写入，避免首次轮询把历史动态全推一遍
        assert plugin.subscriptions[GROUP][0].last_dynamic_id

    async def test_updates_filters_of_existing_subscription(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"group_subscriptions": "12345 = 946974 draw"}},
            handler=feed_handler(),
        )
        plugin._upsert_subscription(GROUP, 946974, ["video"], [], "")
        added, updated = await plugin._apply_group_map()
        assert (added, updated) == (0, 1)
        assert plugin.subscriptions[GROUP][0].filter_types == ["draw"]

    async def test_unchanged_binding_is_noop(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"group_subscriptions": "12345 = 946974 draw"}},
            handler=feed_handler(),
        )
        plugin._upsert_subscription(GROUP, 946974, ["draw"], [], "")
        assert await plugin._apply_group_map() == (0, 0)

    async def test_skips_blocked_session(self, plugin_factory):
        plugin = plugin_factory(
            {
                "permission": {"blocked_groups": "12345"},
                "subscription": {"group_subscriptions": "12345 = 946974"},
            },
            handler=feed_handler(),
        )
        assert await plugin._apply_group_map() == (0, 0)
        assert plugin.subscriptions == {}

    async def test_respects_quota(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"group_subscriptions": "12345 = 946974"}},
            handler=feed_handler(),
        )
        plugin.subscriptions = {GROUP: [Subscription(uid=i) for i in range(1, C.MAX_SUBS_PER_SESSION + 1)]}
        assert await plugin._apply_group_map() == (0, 0)
        assert len(plugin.subscriptions[GROUP]) == C.MAX_SUBS_PER_SESSION

    async def test_counted_as_poll_change(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"group_subscriptions": "12345 = 946974"}},
            handler=feed_handler(),
        )
        plugin.uid_task_gap_seconds = 0
        assert await plugin._poll_once() is True

    async def test_poll_mirrors_declared_mapping(self, plugin_factory):
        config = {"subscription": {"group_subscriptions": "12345 = 946974"}}
        plugin = plugin_factory(config, handler=feed_handler())
        plugin.uid_task_gap_seconds = 0
        await plugin._poll_once()
        assert plugin._last_mirror_text is not None

    async def test_declared_line_overrides_command_filters(self, plugin_factory):
        """手写区声明的过滤器优先：群内改过滤会被下一轮映射重新覆盖（已在文档说明）。"""
        plugin = plugin_factory(
            {"subscription": {"group_subscriptions": "12345 = 946974 forward"}},
            handler=feed_handler(),
        )
        plugin._upsert_subscription(GROUP, 946974, ["draw"], [], "")
        assert await plugin._apply_group_map() == (0, 1)
        assert plugin.subscriptions[GROUP][0].filter_types == ["forward"]

    def test_prune_requires_mirror_baseline(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"group_map_authoritative": True}})
        plugin.subscriptions = {GROUP: [Subscription(uid=1)], "s2": [Subscription(uid=2)]}
        assert plugin._prune_by_group_map() == 0
        assert plugin._sub_count() == 2

    def test_prune_removes_undeclared(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"group_map_authoritative": True, "group_subscriptions": "12345 = 1"}},
        )
        plugin.subscriptions = {GROUP: [Subscription(uid=1)], "s2": [Subscription(uid=2)]}
        plugin._last_mirror_text = "x"
        assert plugin._prune_by_group_map() == 1
        assert list(plugin.subscriptions) == [GROUP]

    def test_prune_disabled_when_not_authoritative(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"group_subscriptions": "12345 = 1"}})
        plugin.subscriptions = {GROUP: [Subscription(uid=9)]}
        plugin._last_mirror_text = "x"
        assert plugin._prune_by_group_map() == 0


class TestGroupMapWriteBack:
    def test_writes_mirror_into_plain_dict_config(self, plugin_factory):
        config = {"subscription": {}}
        plugin = plugin_factory(config)
        plugin.subscriptions = {GROUP: [Subscription(uid=946974, filter_types=["forward"])]}
        assert plugin._sync_group_map_config() is True
        text = config["subscription"]["group_subscriptions"]
        assert C.GROUP_MAP_AUTO_HEADER in text
        assert H.parse_group_map(text) == [GroupBinding(session=GROUP, uid=946974, filter_types=["forward"])]
        assert plugin.group_map_text == text
        assert plugin._last_mirror_text == text
        # 幂等：内容未变化时不再重复写入
        assert plugin._sync_group_map_config() is False

    def test_persists_through_save_config(self, plugin_factory):
        config = RecordingConfig({"subscription": {}})
        plugin = plugin_factory(config)
        plugin.subscriptions = {GROUP: [Subscription(uid=1)]}
        assert plugin._sync_group_map_config() is True
        assert config.saved == 1
        assert "946974" not in config["subscription"]["group_subscriptions"]

    def test_keeps_admin_section_and_skips_blocked(self, plugin_factory):
        config = {"permission": {"blocked_groups": "999"}, "subscription": {"group_subscriptions": "# 手写\n"}}
        plugin = plugin_factory(config)
        plugin.subscriptions = {"aiocqhttp:GroupMessage:999": [Subscription(uid=1)], "s2": [Subscription(uid=2)]}
        assert plugin._sync_group_map_config() is True
        text = config["subscription"]["group_subscriptions"]
        assert text.startswith("# 手写\n")
        assert "UID" not in text
        assert H.parse_group_map(text) == [GroupBinding(session="s2", uid=2)]

    def test_admin_declared_lines_are_not_duplicated(self, plugin_factory):
        config = {"subscription": {"group_subscriptions": "# 手写\n12345 = 672328094 forward\n"}}
        plugin = plugin_factory(config)
        plugin.subscriptions = {
            GROUP: [Subscription(uid=672328094, filter_types=["forward"]), Subscription(uid=2)],
            "aiocqhttp:GroupMessage:946974": [Subscription(uid=946974)],
        }
        assert plugin._sync_group_map_config() is True
        _admin, auto = H.split_group_map(config["subscription"]["group_subscriptions"])
        auto_lines = [line.strip() for line in auto.splitlines() if line.strip()]
        # 手写区声明的 (会话, UID) 不再出现在自动同步区，其余订阅正常镜像
        assert f"{GROUP} = 672328094" not in auto_lines
        assert f"{GROUP} = 2" in auto_lines
        assert "aiocqhttp:GroupMessage:946974 = 946974" in auto_lines

    def test_disabled_by_config_but_force_works(self, plugin_factory):
        config = {"subscription": {"sync_group_map": False}}
        plugin = plugin_factory(config)
        plugin.subscriptions = {GROUP: [Subscription(uid=1)]}
        assert plugin._sync_group_map_config() is False
        assert "group_subscriptions" not in config["subscription"]
        assert plugin._sync_group_map_config(force=True) is True

    def test_no_config_object(self):
        plugin = M.BiliAllPlugin(context=None, config=None)
        plugin.config_obj = None
        assert plugin._sync_group_map_config() is False

    def test_set_cfg_failure_paths(self, plugin_factory):
        plugin = plugin_factory({"subscription": 5})
        assert plugin._set_cfg("subscription.group_subscriptions", "x") is False
        plugin.config_obj = {"subscription": {"nested": 5}}
        assert plugin._set_cfg("subscription.nested.deep", "x") is False
        plugin.config_obj = 5
        assert plugin._set_cfg("a.b", "x") is False
        assert plugin._set_cfg("a", "x") is False

    def test_persist_failure_is_logged(self, plugin_factory):
        config = RecordingConfig({"subscription": {}})
        config.fail = True
        plugin = plugin_factory(config)
        plugin.subscriptions = {GROUP: [Subscription(uid=1)]}
        assert plugin._persist_config() is False
        assert plugin._sync_group_map_config() is True

    async def test_command_writes_back(self, plugin_factory):
        config = {"subscription": {}}
        plugin = plugin_factory(config, handler=feed_handler())
        texts = await collect(plugin.cmd_bili_sub(FakeEvent("/bili_sub 946974")))
        assert "订阅成功" in texts[0].get_plain_text()
        assert "group_subscriptions" in config["subscription"]
        assert GROUP in config["subscription"]["group_subscriptions"]

    async def test_delete_removes_line(self, plugin_factory):
        config = {"subscription": {}}
        plugin = plugin_factory(config, handler=feed_handler())
        await collect(plugin.cmd_bili_sub(FakeEvent("/bili_sub 946974")))
        await collect(plugin.cmd_bili_sub_del(FakeEvent("/bili_sub_del 946974")))
        assert "946974" not in config["subscription"]["group_subscriptions"]

    async def test_global_delete_mirrors(self, plugin_factory):
        config = {"subscription": {}}
        plugin = plugin_factory(config, handler=feed_handler())
        await collect(plugin.cmd_bili_global_sub(FakeEvent("/bili_global_sub 12345 946974", admin=True)))
        assert "946974" in config["subscription"]["group_subscriptions"]
        await collect(plugin.cmd_bili_global_del(FakeEvent("/bili_global_del 12345", admin=True)))
        assert "946974" not in config["subscription"]["group_subscriptions"]

    async def test_initialize_backfills_existing_subscriptions(self, plugin_factory):
        """旧方法（群内指令）建立的订阅在插件启动时就体现在设置面板里。"""
        config = {"subscription": {}}
        plugin = plugin_factory(config)
        plugin._upsert_subscription(GROUP, 946974, ["video"], [], "100")
        plugin._save_subscriptions()
        plugin.subscriptions = {}
        plugin.config_obj = config
        await plugin.initialize()
        try:
            text = config["subscription"]["group_subscriptions"]
            assert H.parse_group_map(text) == [GroupBinding(session=GROUP, uid=946974, filter_types=["video"])]
        finally:
            await plugin.terminate()

    async def test_map_sync_command(self, plugin_factory):
        config = {"subscription": {"group_subscriptions": "12345 = 946974"}}
        plugin = plugin_factory(config, handler=feed_handler())
        texts = await collect(plugin.cmd_bili_map_sync(FakeEvent("/bili_map_sync", admin=True)))
        out = texts[0].get_plain_text()
        assert "新增: 1 条" in out
        assert "配置映射: 1 条 / 1 个会话" in out
        assert "自动同步区回写: 已更新" in out

    async def test_map_sync_reports_errors(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"group_subscriptions": "1 = abc"}})
        texts = await collect(plugin.cmd_bili_map_sync(FakeEvent("/bili_map_sync", admin=True)))
        assert "映射表问题" in texts[0].get_plain_text()

    async def test_map_sync_requires_admin(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await collect(plugin.cmd_bili_map_sync(FakeEvent("/bili_map_sync")))
        assert "无权限" in texts[0].get_plain_text()

    async def test_global_list_shows_mapping_lines(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {GROUP: [Subscription(uid=946974)], "aiocqhttp:GroupMessage:999": [Subscription(uid=1)]}
        plugin.blocked_groups = ("aiocqhttp:GroupMessage:999",)
        texts = await collect(plugin.cmd_bili_global_list(FakeEvent("/bili_global_list", admin=True)))
        out = texts[0].get_plain_text()
        assert "已屏蔽，不推送" in out
        assert f"{GROUP} = 946974" in out

    async def test_global_list_truncates_long_preview(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {f"aiocqhttp:GroupMessage:{900 + i}": [Subscription(uid=i + 1)] for i in range(11)}
        texts = await collect(plugin.cmd_bili_global_list(FakeEvent("/bili_global_list", admin=True)))
        assert "共 11 行" in texts[0].get_plain_text()


class TestGroupMapConfigValidation:
    async def test_initialize_logs_map_problems(self, plugin_factory, caplog):
        plugin = plugin_factory({"subscription": {"group_subscriptions": "1 = abc"}}, handler=feed_handler())
        with caplog.at_level("WARNING"):
            await plugin.initialize()
        try:
            assert plugin.group_map_errors
            assert "群订阅映射表配置有问题" in caplog.text
        finally:
            await plugin.terminate()

    def test_invalid_map_is_an_error(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"group_subscriptions": "946974 = abc"}})
        errors, _warnings = plugin._validate_config()
        assert any("group_subscriptions" in e for e in errors)

    def test_warns_when_mapped_session_is_blocked(self, plugin_factory):
        plugin = plugin_factory(
            {
                "permission": {"blocked_groups": "12345"},
                "subscription": {"group_subscriptions": "12345 = 946974"},
            }
        )
        errors, warnings = plugin._validate_config()
        assert errors == []
        assert any("屏蔽列表" in w for w in warnings)

    def test_warns_authoritative_without_save_config(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"group_map_authoritative": True}})
        _errors, warnings = plugin._validate_config()
        assert any("save_config" in w for w in warnings)

    def test_no_warning_with_save_config(self, plugin_factory):
        plugin = plugin_factory(RecordingConfig({"subscription": {"group_map_authoritative": True}}))
        _errors, warnings = plugin._validate_config()
        assert not any("save_config" in w for w in warnings)

    def test_health_snapshot_fields(self, plugin_factory):
        plugin = plugin_factory(
            {
                "permission": {"blocked_groups": "1\n2"},
                "subscription": {"group_subscriptions": "12345 = 946974"},
            }
        )
        snap = plugin.health_snapshot()
        assert snap["blocked_groups"] == 2
        assert snap["group_map"]["entries"] == 1
        assert snap["group_map"]["sessions"] == 1
        assert snap["group_map"]["synced"] is False
        assert snap["group_map"]["errors"] == 0

    async def test_state_reports_group_switches(self, plugin_factory):
        plugin = plugin_factory(
            {
                "permission": {"blocked_groups": "777"},
                "subscription": {"group_subscriptions": "12345 = 946974"},
            },
            handler=route(default=lambda *_: json_response({"code": -1})),
        )
        texts = await collect(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        out = texts[0].get_plain_text()
        assert "群聊开关" in out
        assert "本会话映射: 1 条" in out
        assert "屏蔽群聊: 1 个" in out

    async def test_state_reports_map_problems(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"group_subscriptions": "946974 = abc"}},
            handler=route(default=lambda *_: json_response({"code": -1})),
        )
        texts = await collect(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        out = texts[0].get_plain_text()
        assert "配置检查未通过" in out
        assert "group_subscriptions" in out
        assert "UID 需为数字" in out

    async def test_state_lists_map_problems_after_warning_only(self, plugin_factory):
        """映射表只有「被屏蔽会话」这类警告时，正常输出全部自检信息。"""
        plugin = plugin_factory(
            {
                "permission": {"blocked_groups": "777"},
                "subscription": {"group_subscriptions": "777 = 946974"},
            },
            handler=route(default=lambda *_: json_response({"code": -1})),
        )
        texts = await collect(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        out = texts[0].get_plain_text()
        assert "映射表问题" not in out
        assert "屏蔽群聊: 1 个" in out
        assert "配置映射: 1 条 / 1 个会话" in out
