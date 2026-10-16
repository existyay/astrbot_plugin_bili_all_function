"""指令 handler 测试（订阅管理 / 管理员指令 / 状态自检）。"""

from __future__ import annotations

import pytest
from conftest import FakeContext, FakeEvent, chain_text, collect, json_response, load_fixture, route

import bili_helpers as H
import main as M
from bili_models import Subscription

FEED = load_fixture("dynamic_feed_jiaran.json")
UID = FEED["_fixture"]["uid"]
LIVE = load_fixture("live_status.json")
LIVE_ROW = LIVE["data"][str(UID)]
NAV = load_fixture("nav_anonymous.json")
NAV_ANON_OK = {"code": 0, "message": "0", "data": {"isLogin": False, "uname": ""}}
NAV_LOGGED_IN = {"code": 0, "message": "0", "data": {"isLogin": True, "uname": "用户"}}

ADMIN_UMO = "aiocqhttp:GroupMessage:12345"


def dynamic_handler(responder=None, nav=None):
    return route(
        ("desktop/v1/feed", responder or (lambda *_: json_response(FEED))),
        ("get_status_info_by_uids", lambda *_: json_response(LIVE)),
        ("getRoomInfoOld", lambda *_: json_response({"code": -1})),
        ("web-interface/nav", lambda *_: json_response(nav or NAV_ANON_OK)),
    )


async def result_texts(agen) -> list[str]:
    return [chain_text([c for c in r.chain if hasattr(c, "text")]) for r in await collect(agen)]


class TestBiliSub:
    async def test_new_subscription(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        event = FakeEvent(f"/bili_sub {UID} video 抽奖")
        texts = await result_texts(plugin.cmd_bili_sub(event))
        assert "订阅成功" in texts[0] and str(UID) in texts[0]
        assert "类型过滤: video" in texts[0]
        assert "正则过滤: 抽奖" in texts[0]
        sub = plugin.subscriptions[ADMIN_UMO][0]
        assert sub.last_dynamic_id == H.dynamic_id(FEED["data"]["items"][1])
        assert sub.is_live is (LIVE_ROW["live_status"] == 1)
        assert plugin.data_path.exists()

    async def test_existing_subscription_is_updated(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        await collect(plugin.cmd_bili_sub(FakeEvent(f"/bili_sub {UID}")))
        event = FakeEvent(f"/bili_sub {UID} draw")
        texts = await result_texts(plugin.cmd_bili_sub(event))
        assert "已订阅" in texts[0]
        assert plugin.subscriptions[ADMIN_UMO][0].filter_types == ["draw"]
        assert len(plugin.subscriptions[ADMIN_UMO]) == 1

    @pytest.mark.parametrize("message", ["/bili_sub", "/bili_sub abc", "/bili_sub "])
    async def test_bad_args(self, plugin_factory, message):
        plugin = plugin_factory({})
        texts = await result_texts(plugin.cmd_bili_sub(FakeEvent(message)))
        assert "参数错误" in texts[0]
        assert plugin.subscriptions == {}


class TestBiliSubDel:
    async def test_delete(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        await collect(plugin.cmd_bili_sub(FakeEvent(f"/bili_sub {UID}")))
        texts = await result_texts(plugin.cmd_bili_sub_del(FakeEvent(f"/bili_sub_del {UID}")))
        assert "已取消订阅" in texts[0]
        assert plugin.subscriptions == {}

    async def test_not_found(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await result_texts(plugin.cmd_bili_sub_del(FakeEvent("/bili_sub_del 123")))
        assert "未找到" in texts[0]

    @pytest.mark.parametrize("message", ["/bili_sub_del", "/bili_sub_del abc"])
    async def test_bad_args(self, plugin_factory, message):
        plugin = plugin_factory({})
        texts = await result_texts(plugin.cmd_bili_sub_del(FakeEvent(message)))
        assert "参数错误" in texts[0]


class TestBiliSubList:
    async def test_empty(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await result_texts(plugin.cmd_bili_sub_list(FakeEvent("/bili_sub_list")))
        assert "没有订阅" in texts[0]

    async def test_layout(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {
            ADMIN_UMO: [
                Subscription(uid=2, is_live=True, filter_types=["video"], filter_regex=["抽奖"]),
                Subscription(uid=3),
            ],
        }
        texts = await result_texts(plugin.cmd_bili_sub_list(FakeEvent("/bili_sub_list")))
        lines = texts[0].splitlines()
        assert lines[0] == "当前会话订阅列表（共 2 个）："
        assert lines[1] == "1) UID 2 ｜ 状态: 直播中 ｜ 过滤: 类型 video、正则 抽奖"
        assert lines[2] == "2) UID 3 ｜ 状态: 未开播 ｜ 过滤: 无"


class TestBiliSubTest:
    async def test_returns_newest_dynamic(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        event = FakeEvent(f"/bili_sub_test {UID}")
        results = await collect(plugin.cmd_bili_sub_test(event))
        assert len(results) == 1
        text = chain_text(results[0].chain)
        assert text.startswith("[B站动态] 嘉然今天吃什么")
        assert H.dynamic_id(FEED["data"]["items"][1]) in text

    async def test_with_filter_note(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        results = await collect(plugin.cmd_bili_sub_test(FakeEvent(f"/bili_sub_test {UID} video")))
        text = chain_text(results[0].chain)
        assert "测试过滤条件: 类型过滤: video" in text

    async def test_all_filtered(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        results = await collect(plugin.cmd_bili_sub_test(FakeEvent(f"/bili_sub_test {UID} draw forward video")))
        assert "都被当前过滤条件过滤掉了" in results[0].get_plain_text()

    async def test_empty_feed(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}},
            handler=route(("desktop/v1/feed", lambda *_: json_response({"code": 0, "data": {"items": []}}))),
        )
        results = await collect(plugin.cmd_bili_sub_test(FakeEvent(f"/bili_sub_test {UID}")))
        assert "未获取到动态数据" in results[0].get_plain_text()

    async def test_bad_args(self, plugin_factory):
        plugin = plugin_factory({})
        results = await collect(plugin.cmd_bili_sub_test(FakeEvent("/bili_sub_test")))
        assert "参数错误" in results[0].get_plain_text()


class TestAdminCommands:
    async def test_global_sub_denied_for_normal_user(self, plugin_factory):
        plugin = plugin_factory({})
        results = await collect(plugin.cmd_bili_global_sub(FakeEvent(f"/bili_global_sub {ADMIN_UMO} 2")))
        assert "无权限" in results[0].get_plain_text()
        assert plugin.subscriptions == {}

    async def test_global_sub_by_config_admin_id(self, plugin_factory):
        plugin = plugin_factory({"permission": {"admin_ids": "10001"}}, handler=dynamic_handler())
        results = await collect(plugin.cmd_bili_global_sub(FakeEvent(f"/bili_global_sub {ADMIN_UMO} {UID}")))
        assert "已添加全局订阅" in results[0].get_plain_text()
        assert plugin.subscriptions[ADMIN_UMO][0].uid == UID

    async def test_global_sub_by_native_admin(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        event = FakeEvent(f"/bili_global_sub {ADMIN_UMO} {UID} video", admin=True)
        results = await collect(plugin.cmd_bili_global_sub(event))
        assert "已添加全局订阅" in results[0].get_plain_text()
        results = await collect(plugin.cmd_bili_global_sub(event))
        assert "已更新全局订阅" in results[0].get_plain_text()

    async def test_global_sub_bad_args(self, plugin_factory):
        plugin = plugin_factory({})
        for message in ("/bili_global_sub", "/bili_global_sub only-session", "/bili_global_sub session abc"):
            results = await collect(plugin.cmd_bili_global_sub(FakeEvent(message, admin=True)))
            assert "参数错误" in results[0].get_plain_text()

    async def test_global_del_all_and_specific(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {ADMIN_UMO: [Subscription(uid=2), Subscription(uid=3)]}
        results = await collect(plugin.cmd_bili_global_del(FakeEvent(f"/bili_global_del {ADMIN_UMO} 2", admin=True)))
        assert "已删除全局订阅" in results[0].get_plain_text()
        assert [s.uid for s in plugin.subscriptions[ADMIN_UMO]] == [3]
        results = await collect(plugin.cmd_bili_global_del(FakeEvent(f"/bili_global_del {ADMIN_UMO}", admin=True)))
        assert "全部订阅" in results[0].get_plain_text()
        assert plugin.subscriptions == {}

    async def test_global_del_edge_cases(self, plugin_factory):
        plugin = plugin_factory({})
        results = await collect(plugin.cmd_bili_global_del(FakeEvent("/bili_global_del", admin=True)))
        assert "参数错误" in results[0].get_plain_text()
        results = await collect(plugin.cmd_bili_global_del(FakeEvent("/bili_global_del nope", admin=True)))
        assert "未找到该会话" in results[0].get_plain_text()
        results = await collect(plugin.cmd_bili_global_del(FakeEvent("/bili_global_del s abc", admin=True)))
        assert "UID 必须是数字" in results[0].get_plain_text()
        plugin.subscriptions = {"s": [Subscription(uid=2)]}
        results = await collect(plugin.cmd_bili_global_del(FakeEvent("/bili_global_del s 9", admin=True)))
        assert "未找到指定订阅" in results[0].get_plain_text()

    async def test_global_del_denied(self, plugin_factory):
        plugin = plugin_factory({})
        results = await collect(plugin.cmd_bili_global_del(FakeEvent("/bili_global_del s")))
        assert "无权限" in results[0].get_plain_text()

    async def test_global_list(self, plugin_factory):
        plugin = plugin_factory({})
        results = await collect(plugin.cmd_bili_global_list(FakeEvent("/bili_global_list", admin=True)))
        assert "没有任何订阅" in results[0].get_plain_text()

        plugin.subscriptions = {
            "s1": [Subscription(uid=2, is_live=True), Subscription(uid=3)],
            "s2": [],
        }
        results = await collect(plugin.cmd_bili_global_list(FakeEvent("/bili_global_list", admin=True)))
        lines = results[0].get_plain_text().splitlines()
        assert lines[0] == "全局订阅列表（2 个会话 / 2 个订阅）："
        assert lines[1] == "▸ s1"
        assert lines[2] == "  1) UID 2 ｜ 状态: 直播中 ｜ 过滤: 无"
        assert lines[3] == "  2) UID 3 ｜ 状态: 未开播 ｜ 过滤: 无"
        assert "s2" not in results[0].get_plain_text()

    async def test_global_list_denied(self, plugin_factory):
        plugin = plugin_factory({})
        results = await collect(plugin.cmd_bili_global_list(FakeEvent("/bili_global_list")))
        assert "无权限" in results[0].get_plain_text()


class TestIsAdmin:
    def test_by_id_and_native(self, plugin_factory):
        plugin = plugin_factory({"permission": {"admin_ids": "10001"}})
        assert plugin._is_admin(FakeEvent(sender_id="10001")) is True
        assert plugin._is_admin(FakeEvent(sender_id="5", admin=True)) is True
        assert plugin._is_admin(FakeEvent(sender_id="5")) is False

    def test_event_without_is_admin(self, plugin_factory):
        plugin = plugin_factory({})

        class Weird:
            def get_sender_id(self):
                return "1"

            def is_admin(self):
                raise RuntimeError("old version")

        assert plugin._is_admin(Weird()) is False


class TestCheckCookie:
    async def test_no_cookie_anonymous(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("nav", lambda *_: json_response(NAV_ANON_OK))))
        status, msg = await plugin._check_cookie()
        assert status == "warn"
        assert "未配置 Cookie" in msg

    async def test_no_cookie_but_logged_in(self, plugin_factory):
        payload = {"code": 0, "data": {"isLogin": True, "uname": "用户"}}
        plugin = plugin_factory({}, handler=route(("nav", lambda *_: json_response(payload))))
        status, msg = await plugin._check_cookie()
        assert status == "warn" and "用户" in msg

    async def test_cookie_valid(self, plugin_factory):
        payload = {"code": 0, "data": {"isLogin": True, "uname": ""}}
        plugin = plugin_factory(
            {"video_download": {"bili_cookie": "SESSDATA=x"}},
            handler=route(("nav", lambda *_: json_response(payload))),
        )
        status, msg = await plugin._check_cookie()
        assert status == "ok" and "未知" in msg

    async def test_cookie_invalid(self, plugin_factory):
        payload = {"code": 0, "data": {"isLogin": False}}
        plugin = plugin_factory(
            {"video_download": {"bili_cookie": "SESSDATA=x"}},
            handler=route(("nav", lambda *_: json_response(payload))),
        )
        status, msg = await plugin._check_cookie()
        assert status == "fail" and "失效" in msg

    async def test_anonymous_nav_error_is_warning(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("nav", lambda *_: json_response({"code": -101}))))
        status, msg = await plugin._check_cookie()
        assert status == "warn" and "匿名模式" in msg and "-101" in msg

    async def test_cookie_nav_error_is_failure(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"bili_cookie": "SESSDATA=x"}},
            handler=route(("nav", lambda *_: json_response({"code": -101}))),
        )
        status, msg = await plugin._check_cookie()
        assert status == "fail" and "失效" in msg

    async def test_network_error(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("nav", lambda *_: json_response({}, status=500))))
        status, msg = await plugin._check_cookie()
        assert status == "fail" and "请求 nav 接口失败" in msg


class TestCheckSubscription:
    async def test_no_subscriptions(self, plugin_factory):
        plugin = plugin_factory({})
        status, msg = await plugin._check_subscription("s1")
        assert status == "warn" and "当前无订阅数据" in msg

    async def test_with_subscription(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        plugin.subscriptions = {"s1": [Subscription(uid=UID)]}

        class Done:
            def done(self):
                return False

        plugin._running = True
        plugin._poll_task = Done()  # type: ignore[assignment]
        status, msg = await plugin._check_subscription("s1")
        assert status == "ok"
        assert "轮询任务运行中" in msg and str(UID) in msg

    async def test_uses_other_session_when_current_is_empty(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        plugin.subscriptions = {"other": [Subscription(uid=UID)]}
        status, msg = await plugin._check_subscription("s1")
        assert status == "warn"  # 轮询任务未运行
        assert "轮询任务未运行" in msg

    async def test_failure_when_nothing_responds(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}},
            handler=route(
                ("desktop/v1/feed", lambda *_: json_response({}, status=500)),
                ("get_status_info_by_uids", lambda *_: json_response({}, status=500)),
                ("getRoomInfoOld", lambda *_: json_response({}, status=500)),
            ),
        )
        plugin.subscriptions = {"s1": [Subscription(uid=UID)]}
        status, msg = await plugin._check_subscription("s1")
        assert status == "fail" and "均无响应" in msg

    async def test_ok_when_only_live_responds(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}},
            handler=route(
                ("desktop/v1/feed", lambda *_: json_response({"code": 0, "data": {"items": []}})),
                ("get_status_info_by_uids", lambda *_: json_response(LIVE)),
            ),
        )
        plugin.subscriptions = {"s1": [Subscription(uid=UID)]}
        status, msg = await plugin._check_subscription("s1")
        assert status == "warn" and "动态0条" in msg


class TestBiliState:
    async def test_config_error_short_circuits(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        monkeypatch.setattr(plugin, "_refresh_config", lambda: None)
        plugin.poll_interval_seconds = 0
        texts = await result_texts(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        assert "配置检查未通过" in texts[0]
        assert "请先修复配置" in texts[0]
        assert "功能开关" not in texts[0]

    async def test_config_error_with_warnings(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({"subscription": {"empty_retry_count": 0}})
        monkeypatch.setattr(plugin, "_refresh_config", lambda: None)
        plugin.video_quality_label = "NOPE"
        texts = await result_texts(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        assert "配置检查未通过" in texts[0]
        assert "配置警告" in texts[0]

    async def test_full_report_ok(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"bili_cookie": "SESSDATA=x"}},
            handler=dynamic_handler(nav=NAV_LOGGED_IN),
        )
        plugin.subscriptions = {
            ADMIN_UMO: [Subscription(uid=UID, last_dynamic_id=H.dynamic_id(FEED["data"]["items"][1]))]
        }

        class Done:
            def done(self):
                return False

        plugin._running = True
        plugin._poll_task = Done()  # type: ignore[assignment]
        texts = await result_texts(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        report = texts[0]
        assert "配置检查通过" in report
        assert "Cookie检查" in report and "订阅检查" in report
        assert "功能开关" in report and "轮询策略" in report
        assert "自检通过" in report

    async def test_full_report_warn(self, plugin_factory):
        plugin = plugin_factory({"subscription": {"empty_retry_count": 0}}, handler=dynamic_handler())
        plugin.subscriptions = {
            ADMIN_UMO: [Subscription(uid=UID, last_dynamic_id=H.dynamic_id(FEED["data"]["items"][1]))]
        }
        texts = await result_texts(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        assert "存在警告" in texts[0]

    async def test_full_report_fail(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"empty_retry_count": 0}},
            handler=route(
                ("desktop/v1/feed", lambda *_: json_response({}, status=500)),
                ("get_status_info_by_uids", lambda *_: json_response({}, status=500)),
                ("getRoomInfoOld", lambda *_: json_response({}, status=500)),
                ("web-interface/nav", lambda *_: json_response({}, status=500)),
            ),
        )
        plugin.subscriptions = {ADMIN_UMO: [Subscription(uid=UID)]}
        texts = await result_texts(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        assert "存在失败项" in texts[0]
        assert "不限制" not in texts[0]

    async def test_report_shows_unlimited_limits(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"max_duration_seconds": 0, "max_size_mb": 0}},
            handler=dynamic_handler(),
        )
        texts = await result_texts(plugin.cmd_bili_state(FakeEvent("/bili_state")))
        assert "- 时长上限: 不限制" in texts[0]
        assert "- 大小上限: 不限制" in texts[0]


class TestPrepareSubscription:
    async def test_existing_subscription_keeps_anchor(self, plugin_factory):
        plugin = plugin_factory({}, handler=dynamic_handler())
        plugin.subscriptions = {ADMIN_UMO: [Subscription(uid=UID, last_dynamic_id="999")]}
        sub, exists = await plugin._prepare_new_subscription(ADMIN_UMO, UID, ["video"], [])
        assert exists is True
        assert sub.last_dynamic_id == "999"

    async def test_live_fetch_failure_keeps_offline(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(
                ("desktop/v1/feed", lambda *_: json_response(FEED)),
                ("live", lambda *_: json_response({}, status=500)),
            ),
        )
        sub, exists = await plugin._prepare_new_subscription(ADMIN_UMO, UID, [], [])
        assert exists is False and sub.is_live is False


class TestEventResultHelpers:
    async def test_plain_result_is_chain(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent("/bili_sub_list")
        results = await collect(plugin.cmd_bili_sub_list(event))
        assert results[0].get_plain_text().startswith("当前会话没有订阅")

    async def test_context_stub_records(self):
        context = FakeContext()
        assert await context.send_message("s", "chain") is True
        assert context.sent == [("s", [])]
        assert context.get_config() == {}
        assert pytest.importorskip("main") is M
