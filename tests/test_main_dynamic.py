"""动态拉取、订阅轮询与推送测试。"""

from __future__ import annotations

import asyncio

import pytest
from conftest import FakeContext, FakeEvent, chain_text, json_response, load_fixture, route

import bili_constants as C
import bili_helpers as H
import main as M
from bili_models import Subscription

FEED = load_fixture("dynamic_feed_jiaran.json")
UID = FEED["_fixture"]["uid"]
LIVE = load_fixture("live_status.json")
LIVE_ROW = LIVE["data"][str(UID)]


def empty_feed():
    return {"code": 0, "message": "0", "data": {"has_more": False, "offset": "", "items": []}}


def feed_responder(*, first_empty: bool = False):
    state = {"n": 0}

    def responder(_method, _url):
        state["n"] += 1
        if first_empty and state["n"] == 1:
            return json_response(empty_feed(), url="https://api/feed")
        return json_response(FEED, url="https://api/feed")

    return responder, state


class TestDynamicItems:
    def test_extracts_and_normalizes(self, plugin_factory):
        plugin = plugin_factory({})
        items = plugin._dynamic_items(FEED)
        assert len(items) == len(FEED["data"]["items"])
        assert isinstance(items[0]["modules"], dict)
        assert H.is_top_dynamic(items[0])

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"code": -1, "data": {"items": [{"id_str": "1"}]}},
            {"code": 0, "data": {"items": "bad"}},
            {"code": 0, "data": "bad"},
            {"code": 0, "data": {"items": [{"no_id": 1}, "bad", None, {"id_str": ""}]}},
        ],
    )
    def test_invalid_payloads(self, plugin_factory, payload):
        plugin = plugin_factory({})
        assert plugin._dynamic_items(payload) == []


class TestFetchDynamicItems:
    async def test_success(self, plugin_factory):
        responder, state = feed_responder()
        plugin = plugin_factory({}, handler=route(("desktop/v1/feed", responder)))
        items = await plugin._fetch_dynamic_items(UID)
        assert items and state["n"] == 1
        url = plugin._http_session.calls[0][1]
        assert f"host_mid={UID}" in url and "timezone_offset=-480" in url and "offset=" in url

    async def test_empty_result_is_retried(self, plugin_factory):
        """回归：接口偶发返回 code=0 且 items 为空时必须重试，否则会漏推最新动态。"""
        responder, state = feed_responder(first_empty=True)
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 2, "empty_retry_delay_seconds": 0}},
            handler=route(("desktop/v1/feed", responder)),
        )
        items = await plugin._fetch_dynamic_items(UID)
        assert items
        assert state["n"] == 2

    async def test_all_empty_returns_empty_list(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 1, "empty_retry_delay_seconds": 0}},
            handler=route(("desktop/v1/feed", lambda *_: json_response(empty_feed()))),
        )
        assert await plugin._fetch_dynamic_items(UID) == []
        assert len(plugin._http_session.calls) == 2

    async def test_zero_retry_makes_single_attempt(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}},
            handler=route(("desktop/v1/feed", lambda *_: json_response(empty_feed()))),
        )
        assert await plugin._fetch_dynamic_items(UID) == []
        assert len(plugin._http_session.calls) == 1

    async def test_silent_412_then_cookie_header(self, plugin_factory):
        state = {"n": 0}

        def responder(_method, _url):
            state["n"] += 1
            if state["n"] == 1:
                return json_response({"code": -352}, status=412)
            return json_response(FEED)

        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}, "video_download": {"bili_cookie": "SESSDATA=x"}},
            handler=route(("desktop/v1/feed", responder)),
        )
        items = await plugin._fetch_dynamic_items(UID)
        assert items
        assert state["n"] == 2

    async def test_network_error_returns_empty(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}},
            handler=route(("desktop/v1/feed", lambda *_: json_response({}, status=500))),
        )
        assert await plugin._fetch_dynamic_items(UID) == []


class TestLiveStatus:
    async def test_from_status_endpoint(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("get_status_info_by_uids", lambda *_: json_response(LIVE))))
        row = await plugin._fetch_live_status(UID)
        assert row and row["uname"] == LIVE_ROW["uname"]
        assert plugin._is_living(row) is (LIVE_ROW["live_status"] == 1)

    async def test_falls_back_to_room_info(self, plugin_factory):
        room = {"code": 0, "data": {"uname": "UP", "roomid": 5, "title": "t", "liveStatus": 1, "cover": "https://c/1"}}
        plugin = plugin_factory(
            {},
            handler=route(
                ("get_status_info_by_uids", lambda *_: json_response({"code": 0, "data": {}})),
                ("getRoomInfoOld", lambda *_: json_response(room)),
            ),
        )
        row = await plugin._fetch_live_status(UID)
        assert row == {
            "uid": UID,
            "uname": "UP",
            "room_id": 5,
            "title": "t",
            "cover": "https://c/1",
            "keyframe": "",
            "live_status": 1,
        }
        assert plugin._is_living(row) is True

    async def test_status_error_falls_back(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(
                ("get_status_info_by_uids", lambda *_: json_response({"code": -1})),
                ("getRoomInfoOld", lambda *_: json_response({"code": -1})),
            ),
        )
        assert await plugin._fetch_live_status(UID) is None

    async def test_status_row_not_dict(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(
                ("get_status_info_by_uids", lambda *_: json_response({"code": 0, "data": {str(UID): "bad"}})),
                ("getRoomInfoOld", lambda *_: json_response({"code": -1})),
            ),
        )
        assert await plugin._fetch_live_status(UID) is None

    async def test_room_defaults(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("getRoomInfoOld", lambda *_: json_response({"code": 0, "data": {}}))),
        )
        row = await plugin._fetch_live_room(UID)
        assert row == {
            "uid": UID,
            "uname": f"UID {UID}",
            "room_id": 0,
            "title": "",
            "cover": "",
            "keyframe": "",
            "live_status": 0,
        }

    async def test_room_data_not_dict(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("getRoomInfoOld", lambda *_: json_response({"code": 0, "data": "bad"}))),
        )
        assert await plugin._fetch_live_room(UID) is None

    async def test_room_uses_user_cover_and_keyframe(self, plugin_factory):
        room = {"code": 0, "data": {"user_cover": "https://u/1", "keyframe": "https://k/1", "liveStatus": "2"}}
        plugin = plugin_factory({}, handler=route(("getRoomInfoOld", lambda *_: json_response(room))))
        row = await plugin._fetch_live_room(UID)
        assert row["cover"] == "https://u/1"
        assert row["keyframe"] == "https://k/1"
        assert row["live_status"] == 2

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(1, True), ("1", True), (0, False), (2, False), (None, False), ("bad", False)],
    )
    def test_is_living(self, plugin_factory, value, expected):
        plugin = plugin_factory({})
        assert plugin._is_living({"live_status": value}) is expected
        assert plugin._is_living(None) is False


class TestUpsertSubscription:
    def test_insert_then_update(self, plugin_factory):
        plugin = plugin_factory({})
        sub, exists = plugin._upsert_subscription("s1", 2, ["video"], ["抽奖"], "100")
        assert exists is False
        assert sub.last_dynamic_id == "100"
        again, exists2 = plugin._upsert_subscription("s1", 2, ["draw"], [], "")
        assert exists2 is True
        assert again is sub
        assert again.filter_types == ["draw"]
        assert again.filter_regex == []
        assert again.last_dynamic_id == "100"  # 更新时保留锚点
        assert plugin._sub_count() == 1

    def test_other_sessions_are_independent(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._upsert_subscription("s1", 2, [], [], "")
        plugin._upsert_subscription("s2", 2, [], [], "")
        assert plugin._sub_count() == 2

    def test_remove(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._upsert_subscription("s1", 2, [], [], "")
        plugin._upsert_subscription("s1", 3, [], [], "")
        assert plugin._remove_subscription("s1", 2) is True
        assert [s.uid for s in plugin.subscriptions["s1"]] == [3]
        assert plugin._remove_subscription("s1", 2) is False
        assert plugin._remove_subscription("s1", 3) is True
        assert "s1" not in plugin.subscriptions
        assert plugin._remove_subscription("nope", 1) is False


class TestProcessSub:
    async def test_pushes_latest_and_marks_all_read(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"dynamic_limit": 1, "recent_ids_limit": 20}})
        sub = Subscription(uid=UID)
        changed = await plugin._process_sub("s1", sub, FEED["data"]["items"], None)
        assert changed is True
        # 只推送最新的一条
        assert len(plugin.context.sent) == 1
        assert chain_text(plugin.context.sent[0][1]).startswith("[B站动态]")
        # 但所有未读动态都被标记为已读
        newest = H.dynamic_id(FEED["data"]["items"][1])
        assert sub.last_dynamic_id == newest
        assert newest in sub.recent_ids
        assert len(sub.recent_ids) == len(FEED["data"]["items"]) - 1  # 去掉置顶

    async def test_no_new_dynamics(self, plugin_factory):
        plugin = plugin_factory({})
        newest = H.dynamic_id(FEED["data"]["items"][1])
        sub = Subscription(uid=UID, last_dynamic_id=newest)
        assert await plugin._process_sub("s1", sub, FEED["data"]["items"], None) is False
        assert plugin.context.sent == []

    async def test_recent_ids_are_respected(self, plugin_factory):
        plugin = plugin_factory({})
        newest = H.dynamic_id(FEED["data"]["items"][1])
        sub = Subscription(uid=UID, recent_ids=[newest])
        assert await plugin._process_sub("s1", sub, FEED["data"]["items"], None) is False

    async def test_recent_ids_lru_keeps_newest(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.recent_ids_limit = 2
        sub = Subscription(uid=UID)
        await plugin._process_sub("s1", sub, FEED["data"]["items"], None)
        assert len(sub.recent_ids) == 2
        assert H.dynamic_id(FEED["data"]["items"][1]) in sub.recent_ids

    async def test_filtered_dynamic_is_marked_read_without_push(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.dynamic_limit = 10
        sub = Subscription(uid=UID, filter_types=["video", "draw", "forward"])
        changed = await plugin._process_sub("s1", sub, FEED["data"]["items"], None)
        assert changed is True
        assert plugin.context.sent == []
        assert sub.last_dynamic_id == H.dynamic_id(FEED["data"]["items"][1])

    async def test_limit_keeps_newest_of_unread(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.dynamic_limit = 2
        sub = Subscription(uid=UID)
        await plugin._process_sub("s1", sub, FEED["data"]["items"], None)
        texts = plugin.context.sent_texts()
        assert len(texts) == 2
        newest = H.dynamic_id(FEED["data"]["items"][1])
        # 第二条（时间正序的最后一条）就是最新动态
        assert newest in texts[-1]

    async def test_live_start_and_stop(self, plugin_factory):
        plugin = plugin_factory({})
        sub = Subscription(uid=UID, last_dynamic_id=H.dynamic_id(FEED["data"]["items"][1]))
        live = dict(LIVE_ROW, live_status=1)
        assert await plugin._process_sub("s1", sub, [], live) is True
        assert sub.is_live is True
        assert "开播了" in plugin.context.sent_texts()[0]

        offline = dict(LIVE_ROW, live_status=0)
        assert await plugin._process_sub("s1", sub, [], offline) is True
        assert sub.is_live is False
        assert "下播了" in plugin.context.sent_texts()[1]

    async def test_live_state_unchanged(self, plugin_factory):
        plugin = plugin_factory({})
        sub = Subscription(uid=UID, is_live=False)
        assert await plugin._process_sub("s1", sub, [], dict(LIVE_ROW, live_status=0)) is False
        assert plugin.context.sent == []

    async def test_live_filter_disables_live_push(self, plugin_factory):
        plugin = plugin_factory({})
        sub = Subscription(uid=UID, filter_types=["live"])
        assert await plugin._process_sub("s1", sub, [], dict(LIVE_ROW, live_status=1)) is False
        assert plugin.context.sent == []
        assert sub.is_live is False

    async def test_live_info_without_uname(self, plugin_factory):
        plugin = plugin_factory({})
        sub = Subscription(uid=UID)
        assert await plugin._process_sub("s1", sub, [], {"live_status": 1}) is True
        assert "UID " in plugin.context.sent_texts()[0]


class TestPolling:
    async def test_poll_once_without_subscriptions(self, plugin_factory):
        plugin = plugin_factory({})
        assert await plugin._poll_once() is False

    async def test_poll_once_pushes(self, plugin_factory):
        responder, _state = feed_responder()
        plugin = plugin_factory(
            {"subscription": {"uid_task_gap_seconds": 0}},
            handler=route(("desktop/v1/feed", responder), ("live", lambda *_: json_response({"code": -1}))),
        )
        plugin.uid_task_gap_seconds = 0
        plugin._upsert_subscription("s1", UID, [], [], "")
        assert await plugin._poll_once() is True
        assert len(plugin.context.sent) == plugin.dynamic_limit

    async def test_poll_once_deduplicates_same_uid_across_sessions(self, plugin_factory):
        responder, state = feed_responder()
        plugin = plugin_factory(
            handler=route(("desktop/v1/feed", responder), ("live", lambda *_: json_response({"code": -1}))),
        )
        plugin.uid_task_gap_seconds = 0
        plugin._upsert_subscription("s1", UID, [], [], "")
        plugin._upsert_subscription("s2", UID, [], [], "")
        await plugin._poll_once()
        feed_calls = [1 for _m, url, _k in plugin._http_session.calls if "desktop/v1/feed" in url]
        assert len(feed_calls) == 1, "同一 UID 只应请求一次"
        assert state["n"] == 1
        assert len(plugin.context.sent) == plugin.dynamic_limit * 2

    async def test_poll_uid_handles_fetch_exceptions(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.uid_task_gap_seconds = 0

        async def boom(_uid):
            raise RuntimeError("network down")

        plugin._fetch_dynamic_items = boom  # type: ignore[assignment]
        plugin._fetch_live_status = boom  # type: ignore[assignment]
        sem = asyncio.Semaphore(1)
        sub = Subscription(uid=UID)
        assert await plugin._poll_uid(UID, [("s1", sub)], sem) is False

    async def test_poll_once_logs_uid_failure(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.uid_task_gap_seconds = 0

        async def boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        plugin._poll_uid = boom  # type: ignore[assignment]
        plugin._upsert_subscription("s1", UID, [], [], "")
        assert await plugin._poll_once() is False

    async def test_poll_loop_saves_on_change(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.poll_interval_seconds = 0
        calls = {"poll": 0, "save": 0}

        async def fake_poll():
            calls["poll"] += 1
            plugin._running = False
            return True

        monkeypatch.setattr(plugin, "_poll_once", fake_poll)
        monkeypatch.setattr(plugin, "_save_subscriptions", lambda: calls.__setitem__("save", calls["save"] + 1))
        sleeps: list[float] = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(M.asyncio, "sleep", fake_sleep)
        plugin._running = True
        await plugin._poll_loop()
        assert calls == {"poll": 1, "save": 1}
        assert sleeps == [0]

    async def test_poll_loop_recovers_from_exception(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.poll_interval_seconds = 0
        calls = {"n": 0}

        async def fake_poll():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            plugin._running = False
            return False

        monkeypatch.setattr(plugin, "_poll_once", fake_poll)
        sleeps: list[float] = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(M.asyncio, "sleep", fake_sleep)
        plugin._running = True
        await plugin._poll_loop()
        assert calls["n"] == 2
        assert sleeps == [5, 0]

    async def test_poll_loop_cancellation(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.poll_interval_seconds = 0
        started = asyncio.Event()

        async def fake_poll():
            started.set()
            return False

        monkeypatch.setattr(plugin, "_poll_once", fake_poll)
        plugin._running = True
        task = asyncio.create_task(plugin._poll_loop())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestLifecycle:
    async def test_initialize_and_terminate(self, plugin_factory):
        plugin = plugin_factory({})
        await plugin.initialize()
        assert plugin._running is True
        assert plugin._poll_task is not None
        plugin._upsert_subscription("s1", UID, [], [], "")
        await plugin.terminate()
        assert plugin._running is False
        assert plugin._poll_task is None
        assert plugin.data_path.exists()

    async def test_terminate_without_task_or_session(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._poll_task = None
        plugin._http_session = None
        await plugin.terminate()
        assert plugin.data_path.exists()

    async def test_start_poll_task_replaces_running_task(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._running = True
        plugin._start_poll_task()
        first = plugin._poll_task
        plugin._start_poll_task()
        assert plugin._poll_task is not first
        await plugin.terminate()

    async def test_initialize_loads_existing_data(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._upsert_subscription("s1", UID, ["video"], [], "1")
        plugin._save_subscriptions()
        plugin.subscriptions = {}
        await plugin.initialize()
        assert plugin._sub_count() == 1
        await plugin.terminate()


class TestContexts:
    async def test_sent_chain_types(self, plugin_factory):
        responder, _ = feed_responder()
        plugin = plugin_factory(
            handler=route(("desktop/v1/feed", responder), ("live", lambda *_: json_response({"code": -1}))),
        )
        plugin.uid_task_gap_seconds = 0
        plugin._upsert_subscription("aiocqhttp:GroupMessage:1", UID, [], [], "")
        await plugin._poll_once()
        session, _chain = plugin.context.sent[0]
        assert session == "aiocqhttp:GroupMessage:1"

    async def test_send_failure_does_not_break_poll(self, plugin_factory):
        responder, _ = feed_responder()
        plugin = plugin_factory(
            context=FakeContext(fail_send=True),
            handler=route(("desktop/v1/feed", responder), ("live", lambda *_: json_response({"code": -1}))),
        )
        plugin.uid_task_gap_seconds = 0
        plugin._upsert_subscription("s1", UID, [], [], "")
        assert await plugin._poll_once() is True
        assert plugin.context.sent == []

    async def test_process_sub_sends_to_given_session(self, plugin_factory):
        plugin = plugin_factory({})
        sub = Subscription(uid=UID)
        await plugin._process_sub("aiocqhttp:PrivateMessage:7", sub, FEED["data"]["items"], None)
        assert plugin.context.sent[0][0] == "aiocqhttp:PrivateMessage:7"
        assert isinstance(FakeEvent(), FakeEvent)
        assert C.PLUGIN_NAME
