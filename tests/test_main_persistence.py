"""订阅数据持久化（加载 / 原子保存 / 边界容错）测试。"""

from __future__ import annotations

import json
from pathlib import Path

import main as M
from bili_models import Subscription


def write_data(plugin, payload) -> None:
    plugin.data_path.parent.mkdir(parents=True, exist_ok=True)
    plugin.data_path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


class TestFsyncDir:
    """目录 fsync 为「尽力而为」：任何平台异常都不得影响订阅保存。"""

    def test_real_directory_never_raises(self, tmp_path):
        M._fsync_dir(tmp_path)  # Windows 走 except 分支，POSIX 真正 fsync

    def test_fsyncs_and_closes_opened_fd(self, monkeypatch):
        calls: list[tuple[str, int]] = []

        def fake_fsync(fd):
            calls.append(("fsync", fd))

        monkeypatch.setattr(M.os, "open", lambda *_a, **_k: 4242)
        monkeypatch.setattr(M.os, "fsync", fake_fsync)
        monkeypatch.setattr(M.os, "close", lambda fd: calls.append(("close", fd)))
        M._fsync_dir(Path("."))
        assert calls == [("fsync", 4242), ("close", 4242)]

    def test_fsync_failure_is_swallowed_and_fd_closed(self, monkeypatch):
        closed: list[int] = []

        def boom(_fd):
            raise OSError("fsync unsupported")

        monkeypatch.setattr(M.os, "open", lambda *_a, **_k: 7)
        monkeypatch.setattr(M.os, "fsync", boom)
        monkeypatch.setattr(M.os, "close", closed.append)
        M._fsync_dir(Path("."))  # 不应抛出
        assert closed == [7]

    def test_unopenable_directory_is_ignored(self, monkeypatch):
        def boom(*_a, **_k):
            raise OSError("directory fsync unsupported on this platform")

        monkeypatch.setattr(M.os, "open", boom)
        M._fsync_dir(Path("."))  # 不应抛出


class TestLoad:
    def test_missing_file_creates_dir(self, plugin_factory):
        plugin = plugin_factory({})
        assert not plugin.data_path.exists()
        plugin._load_subscriptions()
        assert plugin.subscriptions == {}
        assert plugin.data_path.parent.exists()

    def test_valid_roundtrip(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {
            "aiocqhttp:GroupMessage:1": [
                Subscription(
                    uid=2,
                    last_dynamic_id="100",
                    is_live=True,
                    filter_types=["video"],
                    filter_regex=["抽奖"],
                    recent_ids=["100", "99"],
                ),
            ],
        }
        plugin._save_subscriptions()
        plugin.subscriptions = {}
        plugin._load_subscriptions()
        subs = plugin.subscriptions["aiocqhttp:GroupMessage:1"]
        assert len(subs) == 1
        assert subs[0] == Subscription(
            uid=2,
            last_dynamic_id="100",
            is_live=True,
            filter_types=["video"],
            filter_regex=["抽奖"],
            recent_ids=["100", "99"],
        )

    def test_invalid_json(self, plugin_factory):
        plugin = plugin_factory({})
        write_data(plugin, "{not json")
        plugin._load_subscriptions()
        assert plugin.subscriptions == {}

    def test_json_not_dict(self, plugin_factory):
        plugin = plugin_factory({})
        write_data(plugin, "[1, 2]")
        plugin._load_subscriptions()
        assert plugin.subscriptions == {}

    def test_empty_file_and_null(self, plugin_factory):
        plugin = plugin_factory({})
        write_data(plugin, "")
        plugin._load_subscriptions()
        assert plugin.subscriptions == {}
        write_data(plugin, "null")
        plugin._load_subscriptions()
        assert plugin.subscriptions == {}

    def test_skips_bad_entries(self, plugin_factory):
        plugin = plugin_factory({})
        write_data(
            plugin,
            {
                "s1": [
                    {"uid": 1, "filter_types": "bad", "filter_regex": "bad", "recent_ids": "bad"},
                    {"uid": "2"},
                    {"no_uid": 1},
                    "not-a-dict",
                    {"uid": "abc"},
                ],
                "s2": "not-a-list",
                3: [{"uid": 9}],
            },
        )
        plugin._load_subscriptions()
        # JSON 的键总是字符串，因此 3 会被读成 "3"
        assert set(plugin.subscriptions) == {"s1", "3"}
        assert [s.uid for s in plugin.subscriptions["s1"]] == [1, 2]

    def test_filters_are_sanitized(self, plugin_factory):
        plugin = plugin_factory({})
        write_data(
            plugin,
            {
                "s1": [
                    {
                        "uid": 7,
                        "filter_types": ["video", "非法类型", "", "  lottery  "],
                        "filter_regex": ["抽奖", "", "  "],
                        "recent_ids": ["1", "", None, 2],
                        "is_live": 1,
                    }
                ],
            },
        )
        plugin._load_subscriptions()
        sub = plugin.subscriptions["s1"][0]
        assert sub.filter_types == ["video", "lottery"]
        assert sub.filter_regex == ["抽奖"]
        assert sub.recent_ids == ["1", "2"]
        assert sub.is_live is True
        assert sub.last_dynamic_id == ""

    def test_os_error_is_handled(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        write_data(plugin, {"s1": [{"uid": 1}]})

        def boom(*_args, **_kwargs):
            raise OSError("disk on fire")

        monkeypatch.setattr(M.Path, "read_text", boom)
        plugin._load_subscriptions()
        assert plugin.subscriptions == {}


class TestSave:
    def test_atomic_write_uses_tmp_file(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {"s1": [Subscription(uid=1)]}
        plugin._save_subscriptions()
        assert plugin.data_path.exists()
        assert not (plugin.data_path.parent / "subscriptions.json.tmp").exists()
        payload = json.loads(plugin.data_path.read_text(encoding="utf-8"))
        assert payload["__schema__"] == M.SUBSCRIPTIONS_SCHEMA_VERSION
        assert payload["s1"] == [
            {
                "uid": 1,
                "last_dynamic_id": "",
                "is_live": False,
                "filter_types": [],
                "filter_regex": [],
                "recent_ids": [],
            }
        ]

    def test_empty_sessions_are_dropped(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {"s1": [], "s2": [Subscription(uid=2)]}
        plugin._save_subscriptions()
        payload = json.loads(plugin.data_path.read_text(encoding="utf-8"))
        assert [k for k in payload if k != "__schema__"] == ["s2"]

    def test_schema_key_is_not_treated_as_session(self, plugin_factory):
        """保留键不得在加载时被当成会话。"""
        plugin = plugin_factory({})
        write_data(plugin, {"__schema__": 2, "s1": [{"uid": 1}]})
        plugin._load_subscriptions()
        assert list(plugin.subscriptions) == ["s1"]
        assert plugin.data_schema_version == 2

    def test_legacy_file_without_schema_key(self, plugin_factory):
        """旧版数据文件（无版本键）必须能正常加载。"""
        plugin = plugin_factory({})
        write_data(plugin, {"s1": [{"uid": 7}]})
        plugin._load_subscriptions()
        assert plugin.data_schema_version == 1
        assert plugin.subscriptions["s1"][0].uid == 7

    def test_sessions_and_subs_are_capped(self, plugin_factory):
        """超限会话/订阅会被截断并记录告警。"""
        plugin = plugin_factory({})
        raw = {f"s{i}": [{"uid": 1}] for i in range(M.MAX_SESSIONS + 5)}
        raw["s0"] = [{"uid": uid} for uid in range(1, M.MAX_SUBS_PER_SESSION + 5)]
        write_data(plugin, raw)
        plugin._load_subscriptions()
        assert len(plugin.subscriptions) == M.MAX_SESSIONS
        assert len(plugin.subscriptions["s0"]) == M.MAX_SUBS_PER_SESSION

    def test_non_dict_payload_is_rejected(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.data_path.parent.mkdir(parents=True, exist_ok=True)
        plugin.data_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        plugin._load_subscriptions()
        assert plugin.subscriptions == {}

    def test_os_error_is_handled(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.subscriptions = {"s1": [Subscription(uid=1)]}

        def boom(*_args, **_kwargs):
            raise OSError("read-only fs")

        monkeypatch.setattr(M.os, "replace", boom)
        plugin._save_subscriptions()  # 不应抛出
        assert not plugin.data_path.exists()

    def test_sub_count(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._sub_count() == 0
        plugin.subscriptions = {"a": [Subscription(uid=1), Subscription(uid=2)], "b": [Subscription(uid=3)]}
        assert plugin._sub_count() == 3


class TestSessionKey:
    def test_uses_umo(self, plugin_factory):
        from conftest import FakeEvent

        plugin = plugin_factory({})
        assert plugin._session_key(FakeEvent(umo="aiocqhttp:GroupMessage:9")) == "aiocqhttp:GroupMessage:9"

    def test_falls_back_to_sender(self, plugin_factory):
        from conftest import FakeEvent

        plugin = plugin_factory({})
        event = FakeEvent(sender_id="42")
        event.unified_msg_origin = ""
        assert plugin._session_key(event) == "sender:42"
