"""生产化加固行为测试：节流、资源上限、脱敏、可观测性与插件页面接口。"""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import (
    FakeContext,
    FakeEvent,
    FakeResponse,
    FakeSession,
    collect,
    feed_items,
    json_response,
    raising,
    route,
)

import bili_constants as C
import main as M
from bili_models import Subscription


class TestHeaderSecurity:
    def test_user_agent_is_browser_like(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._headers()["User-Agent"] == C.USER_AGENT
        assert "Chrome" in plugin._headers()["User-Agent"]

    def test_cookie_only_for_trusted_hosts(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"bili_cookie": "SESSDATA=abcdef"}})
        trusted = plugin._headers(cookie=True, url="https://api.bilibili.com/x/nav")
        assert trusted["Cookie"] == "SESSDATA=abcdef"
        untrusted = plugin._headers(cookie=True, url="https://evil.example.com/x")
        assert "Cookie" not in untrusted

    def test_cookie_stripped_for_untrusted_request_target(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"bili_cookie": "SESSDATA=abcdef"}})
        headers = plugin._headers(cookie=True)
        stripped = plugin._strip_untrusted_cookie("https://evil.example.com/x", headers)
        assert "Cookie" not in stripped

    def test_cookie_stripped_when_target_trusted(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"bili_cookie": "SESSDATA=abcdef"}})
        headers = plugin._headers(cookie=True)
        kept = plugin._strip_untrusted_cookie("https://api.bilibili.com/x", headers)
        assert kept["Cookie"] == "SESSDATA=abcdef"

    def test_cookie_with_crlf_is_sanitized(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"bili_cookie": "a=1\r\nX-Evil: 1"}})
        assert "\n" not in plugin.bili_cookie and "\r" not in plugin.bili_cookie

    def test_referer_uid(self, plugin_factory):
        plugin = plugin_factory({})
        headers = plugin._headers(referer_uid=703007996)
        assert "space.bilibili.com/703007996" in headers["Referer"]


class TestThrottle:
    async def test_no_wait_when_disabled(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        sleeps: list[float] = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(M.asyncio, "sleep", fake_sleep)
        plugin.min_request_interval_seconds = 0
        await plugin._throttle()
        await plugin._throttle()
        assert sleeps == []

    async def test_waits_between_requests(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        sleeps: list[float] = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(M.asyncio, "sleep", fake_sleep)
        plugin.min_request_interval_seconds = 0.5
        await plugin._throttle()
        await plugin._throttle()
        assert len(sleeps) == 1
        assert 0 < sleeps[0] <= 0.5

    def test_retry_delay_without_base(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.http_retry_delay_seconds = 0
        assert plugin._retry_delay(3) == 0.0

    def test_retry_delay_grows_with_attempt(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.http_retry_delay_seconds = 1.0
        first, third = plugin._retry_delay(1), plugin._retry_delay(3)
        assert 1.0 <= first <= 1.25
        assert 3.0 <= third <= 3.75


class TestBodyLimit:
    async def test_reads_within_limit(self):
        resp = FakeResponse(body=b"abc")
        assert await M.BiliAllPlugin._read_limited(resp, 16) == b"abc"

    async def test_raises_when_over_limit(self):
        resp = FakeResponse(chunks=[b"a" * 10, b"b" * 10])
        with pytest.raises(ValueError, match="上限"):
            await M.BiliAllPlugin._read_limited(resp, 12)

    async def test_zero_limit_reads_all(self):
        resp = FakeResponse(body=b"abc")
        assert await M.BiliAllPlugin._read_limited(resp, 0) == b"abc"

    async def test_empty_chunks_are_skipped(self):
        resp = FakeResponse(chunks=[b"", b"abc", b""])
        assert await M.BiliAllPlugin._read_limited(resp, 64) == b"abc"

    async def test_negative_retry_count_returns_none(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": 0}))))
        assert (
            await plugin._request(
                url="https://api.bilibili.com/x",
                headers={},
                scene="t",
                retry_count=-1,
            )
            is None
        )
        assert plugin.stats["http_requests"] == 0

    async def test_unexpected_exception_is_logged_and_not_retried(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 3, "http_retry_delay_seconds": 0}},
            handler=route(("api", raising(RuntimeError("boom")))),
        )
        assert (
            await plugin._request(
                url="https://api.bilibili.com/x",
                headers={},
                scene="t",
            )
            is None
        )
        assert plugin.stats["http_requests"] == 1
        assert plugin.stats["http_failures"] == 1

    async def test_cancellation_is_propagated(self, plugin_factory):
        plugin = plugin_factory(
            handler=route(("api", raising(asyncio.CancelledError()))),
        )
        with pytest.raises(asyncio.CancelledError):
            await plugin._request(url="https://api.bilibili.com/x", headers={}, scene="t")

    async def test_oversized_response_is_not_retried(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 3, "http_retry_delay_seconds": 0}},
            handler=route(("api", lambda *_: FakeResponse(chunks=[b"x" * 64]))),
        )
        result = await plugin._request(
            url="https://api.bilibili.com/x",
            headers={},
            scene="t",
            max_bytes=8,
        )
        assert result is None
        assert plugin.stats["http_failures"] == 1
        assert plugin.stats["http_requests"] == 1


class TestJsonHardening:
    async def test_non_object_json_is_rejected(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response([1, 2, 3]))))
        assert await plugin._get_json("https://api.bilibili.com/x", {}, scene="t") is None

    async def test_object_json_ok(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": 0}))))
        assert await plugin._get_json("https://api.bilibili.com/x", {}, scene="t") == {"code": 0}


class TestStatsCounters:
    async def test_request_counters(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": 0}))))
        await plugin._get_json("https://api.bilibili.com/x", {}, scene="t")
        assert plugin.stats["http_requests"] == 1
        assert plugin.stats["http_failures"] == 0

    async def test_silent_code_counter(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("api", lambda *_: json_response({"code": 412}, status=412))),
        )
        await plugin._get_json(
            "https://api.bilibili.com/x",
            {},
            scene="t",
            silent_codes={412},
            retry=False,
        )
        assert plugin.stats["http_silent"] == 1
        assert plugin.stats["http_failures"] == 0

    async def test_failure_counter(self, plugin_factory):
        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 0}},
            handler=route(("api", lambda *_: json_response({"code": 500}, status=500))),
        )
        await plugin._get_json("https://api.bilibili.com/x", {}, scene="t")
        assert plugin.stats["http_failures"] == 1

    async def test_send_failure_counter(self, plugin_factory):
        plugin = plugin_factory({}, context=FakeContext(fail_send=True))
        assert await plugin._safe_send("s", [], "scene") is False
        assert plugin.stats["send_failures"] == 1

    async def test_push_counters(self, plugin_factory):
        plugin = plugin_factory({})
        sub = Subscription(uid=703007996)
        item = feed_items("dynamic_feed_asoul.json")[0]
        assert await plugin._process_sub("s1", sub, [item], None) is True
        assert plugin.stats["dynamic_pushes"] == 1
        assert (
            await plugin._process_sub(
                "s1",
                sub,
                [],
                {"live_status": 1, "uname": "A-SOUL"},
            )
            is True
        )
        assert plugin.stats["live_pushes"] == 1

    async def test_live_push_failure_is_not_counted(self, plugin_factory):
        plugin = plugin_factory({}, context=FakeContext(fail_send=True))
        sub = Subscription(uid=2, is_live=True)
        assert (
            await plugin._process_sub(
                "s1",
                sub,
                [],
                {"live_status": 2, "uname": "UP"},
            )
            is True
        )
        assert plugin.stats["live_pushes"] == 0
        assert plugin.stats["send_failures"] == 1

    async def test_poll_cycle_and_error_counters(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.subscriptions = {"s1": [Subscription(uid=1)]}

        async def boom(*_args, **_kwargs):
            raise RuntimeError("nope")

        monkeypatch.setattr(plugin, "_poll_uid", boom)
        await plugin._poll_once()
        assert plugin.stats["poll_cycles"] == 1
        assert plugin.stats["poll_errors"] == 1

    async def test_poll_loop_error_counter(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.poll_interval_seconds = 0
        calls = {"n": 0}

        async def fake_poll():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            plugin._running = False
            return False

        async def fake_sleep(_seconds):
            return None

        monkeypatch.setattr(plugin, "_poll_once", fake_poll)
        monkeypatch.setattr(M.asyncio, "sleep", fake_sleep)
        plugin._running = True
        await plugin._poll_loop()
        assert plugin.stats["poll_errors"] == 1


class TestHealthSnapshot:
    def test_snapshot_shape(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"bili_cookie": "SESSDATA=abcdef1234"}})
        plugin.subscriptions = {"s1": [Subscription(uid=2)]}
        snap = plugin.health_snapshot()
        assert snap["plugin"] == C.PLUGIN_NAME
        assert snap["version"] == C.PLUGIN_VERSION
        assert snap["subscriptions"] == 1
        assert snap["sessions"] == 1
        assert snap["healthy"] is False  # 未启动轮询
        assert snap["cookie_configured"] is True
        assert snap["cookie_masked"] == "SESSDATA=abcd***"
        assert "abcdef1234" not in json.dumps(snap, ensure_ascii=False)
        assert set(snap["features"]) == {"parse", "video_info", "download", "llm_tool"}
        assert snap["poll"]["interval_seconds"] == plugin.poll_interval_seconds

    def test_snapshot_is_json_serializable(self, plugin_factory):
        plugin = plugin_factory({})
        assert json.loads(json.dumps(plugin.health_snapshot(), ensure_ascii=False))

    async def test_healthy_after_start(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._running = True
        task = asyncio.create_task(asyncio.sleep(10))
        plugin._poll_task = task
        try:
            assert plugin.health_snapshot()["healthy"] is True
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


class TestWebApiRegistration:
    async def test_registered_on_initialize(self, plugin_factory):
        ctx = FakeContext()
        plugin = plugin_factory({}, context=ctx)
        await plugin.initialize()
        assert len(ctx.web_apis) == 1
        route_path, handler, methods, desc = ctx.web_apis[0]
        assert route_path == C.WEB_API_STATUS_ROUTE
        assert methods == ["GET"]
        assert desc and callable(handler)
        await plugin.terminate()

    async def test_registration_is_idempotent(self, plugin_factory):
        ctx = FakeContext()
        plugin = plugin_factory({}, context=ctx)
        plugin._register_web_api()
        plugin._register_web_api()
        assert len(ctx.web_apis) == 1

    def test_registration_failure_is_swallowed(self, plugin_factory):
        ctx = FakeContext()
        ctx.fail_web_api = True
        plugin = plugin_factory({}, context=ctx)
        plugin._register_web_api()  # 不应抛出
        assert plugin._web_api_registered is False
        assert ctx.web_apis == []

    def test_legacy_context_without_register_web_api(self, plugin_factory, monkeypatch):
        ctx = FakeContext()
        monkeypatch.delattr(FakeContext, "register_web_api", raising=False)
        plugin = plugin_factory({}, context=ctx)
        plugin._register_web_api()  # 旧版本 AstrBot：静默跳过
        assert plugin._web_api_registered is False

    async def test_handler_returns_json_payload(self, plugin_factory):
        ctx = FakeContext()
        plugin = plugin_factory({}, context=ctx)
        response = await plugin._web_status_handler()
        body = json.loads(bytes(response.body).decode("utf-8"))
        assert body["status"] == "ok"
        assert body["data"]["plugin"] == C.PLUGIN_NAME
        assert response.status_code == 200

    async def test_handler_falls_back_without_web_module(self, plugin_factory, monkeypatch):
        import builtins

        plugin = plugin_factory({})
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "astrbot.api.web":
                raise ImportError("no web module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert (await plugin._web_status_handler())["plugin"] == C.PLUGIN_NAME


class TestLifecycle:
    async def test_terminate_closes_session_and_saves(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {"s1": [Subscription(uid=2)]}
        plugin._running = True
        await plugin.terminate()
        assert plugin._running is False
        assert plugin._http_session is None or plugin._http_session.closed
        assert plugin.data_path.exists()

    async def test_terminate_without_task_or_session(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._http_session = None
        await plugin.terminate()
        await plugin.terminate()  # 幂等

    async def test_terminate_tolerates_task_exception(self, plugin_factory):
        plugin = plugin_factory({})

        async def stubborn():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise RuntimeError("poll exploded after cancel") from None

        plugin._poll_task = asyncio.create_task(stubborn())
        await asyncio.sleep(0)
        await plugin.terminate()
        assert plugin._poll_task is None

    async def test_terminate_tolerates_close_error(self, plugin_factory):
        plugin = plugin_factory({})

        class BadSession(FakeSession):
            async def close(self):
                raise RuntimeError("close failed")

        plugin._http_session = BadSession(None)
        plugin._http_session.closed = False
        await plugin.terminate()

    async def test_initialize_is_safe_twice(self, plugin_factory):
        plugin = plugin_factory({})
        await plugin.initialize()
        await plugin.initialize()
        assert plugin._running is True
        await plugin.terminate()


class TestSubscriptionQuota:
    def test_existing_subscription_is_not_blocked(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {"s1": [Subscription(uid=2)]}
        assert plugin._check_subscription_quota("s1", 2) == ""

    def test_per_session_cap(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {
            "s1": [Subscription(uid=i) for i in range(1, C.MAX_SUBS_PER_SESSION + 1)],
        }
        assert "上限" in plugin._check_subscription_quota("s1", 999)

    def test_session_cap(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.subscriptions = {f"s{i}": [Subscription(uid=1)] for i in range(C.MAX_SESSIONS)}
        assert "会话数已达上限" in plugin._check_subscription_quota("new-session", 5)

    async def test_command_reports_quota(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.subscriptions = {
            "aiocqhttp:GroupMessage:12345": [Subscription(uid=i) for i in range(1, C.MAX_SUBS_PER_SESSION + 1)],
        }
        monkeypatch.setattr(plugin, "_prepare_new_subscription", _should_not_run)
        texts = await collect(plugin.cmd_bili_sub(FakeEvent("/bili_sub 999")))
        assert "上限" in texts[0].get_plain_text()

    async def test_global_command_reports_quota(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})
        plugin.subscriptions = {
            "target-session": [Subscription(uid=i) for i in range(1, C.MAX_SUBS_PER_SESSION + 1)],
        }
        monkeypatch.setattr(plugin, "_prepare_new_subscription", _should_not_run)
        texts = await collect(
            plugin.cmd_bili_global_sub(
                FakeEvent("/bili_global_sub target-session 999", admin=True),
            ),
        )
        assert "上限" in texts[0].get_plain_text()


async def _should_not_run(*_args, **_kwargs):  # pragma: no cover - 仅用于断言不被调用
    raise AssertionError("不应触达订阅写入")


class TestCommandValidation:
    async def test_invalid_regex_is_reported(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await collect(plugin.cmd_bili_sub(FakeEvent("/bili_sub 123 re:[unclosed")))
        assert "过滤器无效" in texts[0].get_plain_text()

    async def test_invalid_regex_on_test_command(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await collect(plugin.cmd_bili_sub_test(FakeEvent("/bili_sub_test 123 re:[x")))
        assert "过滤器无效" in texts[0].get_plain_text()

    async def test_invalid_regex_on_global_command(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await collect(
            plugin.cmd_bili_global_sub(FakeEvent("/bili_global_sub sess 123 re:[x", admin=True)),
        )
        assert "过滤器无效" in texts[0].get_plain_text()

    async def test_out_of_range_uid_is_rejected(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await collect(plugin.cmd_bili_sub(FakeEvent(f"/bili_sub {C.MAX_UID + 1}")))
        assert "参数错误" in texts[0].get_plain_text()

    async def test_delete_rejects_out_of_range_uid(self, plugin_factory):
        plugin = plugin_factory({})
        texts = await collect(plugin.cmd_bili_sub_del(FakeEvent(f"/bili_sub_del {C.MAX_UID + 1}")))
        assert "参数错误" in texts[0].get_plain_text()


class TestInputLimits:
    def test_nested_list_without_urls(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._extract_links_from_json({"a": [1, 2, None]}) == []
        assert plugin._extract_links_from_json({"a": []}) == []
        assert plugin._extract_links_from_json({"a": [{"b": 1}]}) == []

    async def test_overlong_message_is_truncated_before_scan(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent("x" * (C.MAX_MESSAGE_SCAN_LEN + 100) + " BV1zKJQ6fEaY")
        await plugin.handle_bili_parse(event)
        assert event.stopped is False

    async def test_link_within_first_4000_chars_is_parsed(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(
                (
                    "view",
                    lambda *_: json_response(
                        {"code": 0, "data": {"bvid": "BV1zKJQ6fEaY", "title": "t", "cid": 1, "pages": [{"cid": 1}]}},
                    ),
                )
            ),
        )
        event = FakeEvent("BV1zKJQ6fEaY " + "x" * (C.MAX_MESSAGE_SCAN_LEN + 50))
        await plugin.handle_bili_parse(event)
        assert event.stopped is True

    async def test_llm_tool_rejects_overlong_input(self, plugin_factory):
        plugin = plugin_factory({})
        result = await plugin.llm_bili_video_info(
            FakeEvent("/x"),
            "BV1zKJQ6fEaY" + "x" * C.MAX_LLM_URL_LEN,
        )
        assert "输入过长" in result


class TestConfigValidation:
    def test_new_warnings(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.min_request_interval_seconds = 0
        plugin.uid_concurrency = 16
        plugin.recent_ids_limit = 5
        errors, warnings = plugin._validate_config()
        assert errors == []
        joined = "\n".join(warnings)
        assert "min_request_interval_seconds" in joined
        assert "并发过高" in joined

    def test_recent_ids_warning(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.recent_ids_limit = 2
        _errors, warnings = plugin._validate_config()
        assert any("recent_ids_limit" in w for w in warnings)

    def test_overlong_cookie_is_error(self, plugin_factory):
        plugin = plugin_factory({})
        plugin.bili_cookie = "SESSDATA=" + "a" * 5000
        errors, _warnings = plugin._validate_config()
        assert any("过长" in e for e in errors)

    async def test_state_reports_stats_and_masked_cookie(self, plugin_factory, monkeypatch):
        plugin = plugin_factory(
            {"video_download": {"bili_cookie": "SESSDATA=abcdef1234"}},
            handler=route(
                (
                    "nav",
                    lambda *_: json_response(
                        {"code": 0, "data": {"isLogin": True, "uname": "tester"}},
                    ),
                ),
                ("space", lambda *_: json_response({"code": 0, "data": {"items": []}})),
                ("live", lambda *_: json_response({"code": 0, "data": {"live_status": 2}})),
            ),
        )
        monkeypatch.setattr(plugin, "_refresh_config", lambda: None)
        plugin.min_request_interval_seconds = 0.2
        text = (await collect(plugin.cmd_bili_state(FakeEvent("/bili_state"))))[0].get_plain_text()
        assert "运行统计" in text
        assert "SESSDATA=abcd***" in text
        assert "abcdef1234" not in text
