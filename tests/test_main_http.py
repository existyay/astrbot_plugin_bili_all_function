"""HTTP 层、JSON 提取与视频引用解析测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import aiohttp
import astrbot.api.message_components as Comp
import pytest
from conftest import FakeEvent, FakeResponse, json_response, route

import main as M


class TestClassifyExc:
    def _err(self, status: int) -> aiohttp.ClientResponseError:
        return aiohttp.ClientResponseError(
            request_info=SimpleNamespace(real_url="https://x/"),
            history=(),
            status=status,
            message="m",
        )

    @pytest.mark.parametrize(
        ("status", "cat", "retryable"),
        [
            (404, "http_client_404", False),
            (412, "http_client_412", False),
            (503, "http_server_503", True),
            (429, "http_client_429", True),
            (302, "http_302", False),
        ],
    )
    def test_response_errors(self, status, cat, retryable):
        assert M.BiliAllPlugin._classify_exc(self._err(status)) == (cat, retryable)

    def test_other_errors(self):
        assert M.BiliAllPlugin._classify_exc(TimeoutError()) == ("timeout", True)
        assert M.BiliAllPlugin._classify_exc(aiohttp.ClientConnectionError()) == ("network", True)
        assert M.BiliAllPlugin._classify_exc(aiohttp.ClientPayloadError()) == ("network", True)
        assert M.BiliAllPlugin._classify_exc(ValueError("x")) == ("unknown", False)


class TestSession:
    async def test_ensure_reuses_session(self, plugin_factory):
        plugin = plugin_factory({})
        session = await plugin._ensure_http_session()
        assert session is plugin._http_session
        assert await plugin._ensure_http_session() is session

    async def test_creates_real_session_when_missing(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._http_session = None
        session = await plugin._ensure_http_session()
        assert isinstance(session, aiohttp.ClientSession)
        await session.close()

    async def test_closed_session_is_replaced(self, plugin_factory):
        plugin = plugin_factory({})
        plugin._http_session.closed = True
        new_session = await plugin._ensure_http_session()
        assert isinstance(new_session, aiohttp.ClientSession)
        await new_session.close()


class TestRequest:
    async def test_success(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("api", lambda *_: json_response({"code": 0}, url="https://api/x"))),
        )
        result = await plugin._request(url="https://api/x", headers={}, scene="t")
        assert result and result["status"] == 200
        assert json.loads(result["body"]) == {"code": 0}
        assert result["final_url"] == "https://api/x"

    async def test_read_body_false(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": 0}))))
        result = await plugin._request(url="https://api/x", headers={}, scene="t", read_body=False)
        assert result and result["body"] == b""

    async def test_silent_code_by_status(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": -1}, status=412))))
        assert (
            await plugin._request(
                url="https://api/x",
                headers={},
                scene="t",
                silent_codes={412},
            )
            is None
        )

    async def test_silent_code_by_exception(self, plugin_factory):
        err = aiohttp.ClientResponseError(
            request_info=SimpleNamespace(real_url="https://x/"),
            history=(),
            status=412,
            message="",
        )
        plugin = plugin_factory({}, handler=route(("api", lambda *_: err)))
        assert (
            await plugin._request(
                url="https://api/x",
                headers={},
                scene="t",
                silent_codes={412},
            )
            is None
        )

    async def test_retry_then_success(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            if calls["n"] == 1:
                return json_response({"code": -1}, status=503)
            return json_response({"code": 0})

        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 2, "http_retry_delay_seconds": 0}}, handler=route(("api", responder))
        )
        result = await plugin._request(url="https://api/x", headers={}, scene="t")
        assert result and calls["n"] == 2

    async def test_gives_up_after_retries(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            return json_response({"code": -1}, status=500)

        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 1, "http_retry_delay_seconds": 0}}, handler=route(("api", responder))
        )
        assert await plugin._request(url="https://api/x", headers={}, scene="t") is None
        assert calls["n"] == 2

    async def test_non_retryable_status(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            return json_response({"code": -1}, status=404)

        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 3, "http_retry_delay_seconds": 0}}, handler=route(("api", responder))
        )
        assert await plugin._request(url="https://api/x", headers={}, scene="t") is None
        assert calls["n"] == 1

    async def test_retry_disabled(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            return json_response({"code": -1}, status=500)

        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 3, "http_retry_delay_seconds": 0}}, handler=route(("api", responder))
        )
        assert await plugin._request(url="https://api/x", headers={}, scene="t", retry=False) is None
        assert calls["n"] == 1

    async def test_timeout_is_retried(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            return TimeoutError()

        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 1, "http_retry_delay_seconds": 0}}, handler=route(("api", responder))
        )
        assert await plugin._request(url="https://api/x", headers={}, scene="t") is None
        assert calls["n"] == 2

    async def test_unknown_exception_not_retried(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            return ValueError("boom")

        plugin = plugin_factory({"subscription": {"http_retry_count": 2}}, handler=route(("api", responder)))
        assert await plugin._request(url="https://api/x", headers={}, scene="t") is None
        assert calls["n"] == 1

    async def test_explicit_retry_count(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            return json_response({"code": -1}, status=500)

        plugin = plugin_factory(
            {"subscription": {"http_retry_count": 0, "http_retry_delay_seconds": 0}}, handler=route(("api", responder))
        )
        await plugin._request(url="https://api/x", headers={}, scene="t", retry_count=2)
        assert calls["n"] == 3


class TestGetJson:
    async def test_ok_with_params(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("a=1", lambda *_: json_response({"code": 0, "data": {"x": 1}}))))
        assert await plugin._get_json("https://api/x", {"a": 1}, scene="t") == {"code": 0, "data": {"x": 1}}
        _method, url, _kwargs = plugin._http_session.calls[0]
        assert url == "https://api/x?a=1"

    async def test_no_params(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api/x", lambda *_: json_response({"code": 0}))))
        assert await plugin._get_json("https://api/x", {}, scene="t") == {"code": 0}
        assert plugin._http_session.calls[0][1] == "https://api/x"

    async def test_bad_json(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("api", lambda *_: FakeResponse(body=b"<html>not json</html>"))),
        )
        assert await plugin._get_json("https://api/x", {}, scene="t") is None

    async def test_request_failed(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": -1}, status=404))))
        assert await plugin._get_json("https://api/x", {}, scene="t") is None

    async def test_non_utf8_body(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: FakeResponse(body=b"\xff\xfe{}"))))
        assert await plugin._get_json("https://api/x", {}, scene="t") == {}


class TestGetJsonCandidates:
    async def test_second_candidate_wins(self, plugin_factory):
        calls = {"n": 0}

        def responder(_method, _url):
            calls["n"] += 1
            if calls["n"] == 1:
                return json_response({"code": -1}, status=404)
            return json_response({"code": 0, "data": {"items": [1]}})

        plugin = plugin_factory({}, handler=route(("api", responder)))
        payload = await plugin._get_json_candidates(
            "https://api/x",
            {},
            [{"A": "1"}, {"B": "2"}],
            scene="t",
            retry=False,
        )
        assert payload == {"code": 0, "data": {"items": [1]}}
        assert calls["n"] == 2

    async def test_all_candidates_fail(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": -1}, status=404))))
        assert (
            await plugin._get_json_candidates(
                "https://api/x",
                {},
                [{"A": "1"}, {"B": "2"}],
                scene="t",
                retry=False,
            )
            is None
        )


class TestCoerceJson:
    def test_variants(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._coerce_json('{"a": 1}') == {"a": 1}
        assert plugin._coerce_json("  ") is None
        assert plugin._coerce_json("not json") is None
        assert plugin._coerce_json({"data": {"a": 1}}) == {"a": 1}
        assert plugin._coerce_json({"a": 1}) == {"a": 1}
        assert plugin._coerce_json([{"a": 1}]) == {"a": 1}
        assert plugin._coerce_json([None]) is None
        assert plugin._coerce_json(None) is None
        assert plugin._coerce_json(5) is None
        assert plugin._coerce_json(SimpleNamespace(data={"a": 1})) == {"a": 1}
        assert plugin._coerce_json("1" * 10, _depth=9) is None


class TestExtractLinksFromJson:
    def test_nested_dict(self, plugin_factory):
        plugin = plugin_factory({})
        payload = {
            "meta": {"detail_1": {"qqdocurl": "https://b23.tv/abc?x=1"}},
            "nested": {"list": ["https://www.bilibili.com/video/BV1xx411c7mD"]},
        }
        links = plugin._extract_links_from_json(payload)
        assert "https://b23.tv/abc?x=1" in links
        assert "https://www.bilibili.com/video/BV1xx411c7mD" in links

    def test_json_string_and_dedup(self, plugin_factory):
        plugin = plugin_factory({})
        payload = {"a": "https://b23.tv/x", "b": ["https://b23.tv/x"]}
        links = plugin._extract_links_from_json(json.dumps(payload))
        assert links == ["https://b23.tv/x"]

    def test_news_section_and_empty(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._extract_links_from_json({"meta": {"news": {"jumpUrl": "https://b23.tv/n"}}}) == [
            "https://b23.tv/n",
        ]
        assert plugin._extract_links_from_json({"meta": {"news": "bad"}}) == []
        assert plugin._extract_links_from_json("bad json") == []

    def test_list_payload(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._extract_links_from_json([{"url": "https://b23.tv/l"}]) == ["https://b23.tv/l"]


class TestExtractJsonLinksFromEvent:
    def test_no_message_obj(self, plugin_factory):
        plugin = plugin_factory({})
        assert plugin._extract_json_links_from_event(SimpleNamespace()) == []

    def test_dict_component(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent(
            messages=[
                {"type": "reply", "data": {"id": "1"}},
                {"type": "json", "data": {"meta": {"detail_1": {"qqdocurl": "https://b23.tv/dict"}}}},
                {"type": "text", "data": {"text": "https://b23.tv/ignored"}},
            ]
        )
        assert plugin._extract_json_links_from_event(event) == ["https://b23.tv/dict"]

    def test_component_object(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent(
            messages=[
                Comp.Json(data={"meta": {"detail_1": {"qqdocurl": "https://b23.tv/obj"}}}),
                Comp.Plain("https://b23.tv/plain"),
            ]
        )
        assert plugin._extract_json_links_from_event(event) == ["https://b23.tv/obj"]

    def test_json_component_with_direct_payload(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent(messages=[SimpleNamespace(type="json", data={"url": "https://b23.tv/direct"})])
        assert plugin._extract_json_links_from_event(event) == ["https://b23.tv/direct"]


class TestResolveShortUrl:
    async def test_follows_redirect(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("b23.tv", lambda *_: FakeResponse(url="https://www.bilibili.com/video/BV1xx411c7mD"))),
        )
        assert await plugin._resolve_short_url("https://b23.tv/x") == "https://www.bilibili.com/video/BV1xx411c7mD"

    async def test_returns_original_on_failure(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("b23.tv", lambda *_: json_response({}, status=404))))
        assert await plugin._resolve_short_url("https://b23.tv/x") == "https://b23.tv/x"


class TestExtractVideoRef:
    async def test_bv(self, plugin_factory):
        plugin = plugin_factory({})
        ref = await plugin._extract_video_ref("看 BV1GJ411x7h7")
        assert ref == {"bvid": "BV1GJ411x7h7", "aid": None, "source": "看 BV1GJ411x7h7", "page": 0}

    async def test_av_and_page(self, plugin_factory):
        plugin = plugin_factory({})
        ref = await plugin._extract_video_ref("https://www.bilibili.com/video/av123?p=4")
        assert ref and ref["aid"] == 123 and ref["bvid"] is None and ref["page"] == 3

    async def test_short_link(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("b23.tv", lambda *_: FakeResponse(url="https://www.bilibili.com/video/BV1xx411c7mD?p=2"))),
        )
        ref = await plugin._extract_video_ref("https://b23.tv/abc")
        assert ref and ref["bvid"] == "BV1xx411c7mD" and ref["page"] == 1

    async def test_no_match(self, plugin_factory):
        plugin = plugin_factory({})
        assert await plugin._extract_video_ref("https://example.com/x") is None

    async def test_from_tokens(self, plugin_factory):
        plugin = plugin_factory({})
        assert await plugin._extract_video_ref_from_tokens([]) is None
        ref = await plugin._extract_video_ref_from_tokens(["https://example.com", "av9"])
        assert ref and ref["aid"] == 9


class TestRequestErrorHelpers:
    async def test_session_calls_are_recorded(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("api", lambda *_: json_response({"code": 0}))))
        await plugin._get_json("https://api/x", {"a": "b"}, scene="t")
        method, _url, kwargs = plugin._http_session.calls[0]
        assert method == "GET"
        assert kwargs["allow_redirects"] is True
