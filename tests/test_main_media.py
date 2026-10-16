"""视频信息获取、播放地址解析与下载发送测试。"""

from __future__ import annotations

import aiohttp
import astrbot.api.message_components as Comp
import pytest
from conftest import FakeEvent, FakeResponse, json_response, load_fixture, route

VIEW = load_fixture("video_view.json")
PLAYURL = load_fixture("playurl.json")
BVID = VIEW["data"]["bvid"]


class TestGetVideoInfo:
    async def test_by_bvid(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("view?", lambda *_: json_response(VIEW))))
        info = await plugin._get_video_info({"bvid": BVID})
        assert info and info["bvid"] == BVID
        assert "bvid=" in plugin._http_session.calls[0][1]

    async def test_by_aid(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("view?", lambda *_: json_response(VIEW))))
        info = await plugin._get_video_info({"aid": 123})
        assert info is not None
        assert "aid=123" in plugin._http_session.calls[0][1]

    async def test_without_ids(self, plugin_factory):
        plugin = plugin_factory({})
        assert await plugin._get_video_info({}) is None
        assert plugin._http_session.calls == []

    async def test_api_error(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("view?", lambda *_: json_response({"code": -404}))))
        assert await plugin._get_video_info({"bvid": BVID}) is None

    async def test_network_failure(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("view?", lambda *_: json_response({}, status=500))))
        assert await plugin._get_video_info({"bvid": BVID}, retry_count=0) is None


class TestFetchPlayurl:
    async def test_durl_fixture(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("playurl", lambda *_: json_response(PLAYURL))))
        stream = await plugin._fetch_playurl(bvid=BVID, cid=1, qn=64)
        assert stream and stream["url"].startswith("https://")
        assert "fnval=0" in plugin._http_session.calls[0][1]

    async def test_dash_base_url_variant(self, plugin_factory):
        payload = {"code": 0, "data": {"dash": {"video": [{"base_url": "https://d/1.m4s", "id": 80}]}}}
        plugin = plugin_factory({}, handler=route(("playurl", lambda *_: json_response(payload))))
        assert await plugin._fetch_playurl(bvid=BVID, cid=1, qn=64) == {
            "url": "https://d/1.m4s",
            "quality": 80,
        }

    async def test_dash_base_url_camel(self, plugin_factory):
        payload = {"code": 0, "data": {"dash": {"video": [{"baseUrl": "https://d/2.m4s", "id": 64}]}}}
        plugin = plugin_factory({}, handler=route(("playurl", lambda *_: json_response(payload))))
        assert (await plugin._fetch_playurl(bvid=BVID, cid=1, qn=64))["url"] == "https://d/2.m4s"

    async def test_durl_empty_url_falls_through_to_dash(self, plugin_factory):
        payload = {
            "code": 0,
            "data": {
                "durl": [{"url": "   "}],
                "dash": {"video": [{"baseUrl": "https://d/3.m4s", "id": 32}]},
            },
        }
        plugin = plugin_factory({}, handler=route(("playurl", lambda *_: json_response(payload))))
        assert (await plugin._fetch_playurl(bvid=BVID, cid=1, qn=64))["url"] == "https://d/3.m4s"

    @pytest.mark.parametrize(
        "payload",
        [
            {"code": -404, "data": {}},
            {"code": 0, "data": {}},
            {"code": 0, "data": {"durl": [], "dash": {"video": []}}},
            {"code": 0, "data": {"durl": "bad", "dash": "bad"}},
        ],
    )
    async def test_no_stream(self, plugin_factory, payload):
        plugin = plugin_factory({}, handler=route(("playurl", lambda *_: json_response(payload))))
        assert await plugin._fetch_playurl(bvid=BVID, cid=1, qn=64) is None


class TestProbeContentLength:
    async def test_with_header(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=route(("upos", lambda *_: FakeResponse(headers={"Content-Length": "2048"}))),
        )
        assert await plugin._probe_content_length("https://upos/x") == 2048
        assert plugin._http_session.calls[0][0] == "HEAD"

    async def test_without_header_or_bad_value(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("upos", lambda *_: FakeResponse())))
        assert await plugin._probe_content_length("https://upos/x") is None
        plugin2 = plugin_factory(
            {},
            handler=route(("upos", lambda *_: FakeResponse(headers={"Content-Length": "abc"}))),
        )
        assert await plugin2._probe_content_length("https://upos/x") is None

    async def test_request_failed(self, plugin_factory):
        plugin = plugin_factory({}, handler=route(("upos", lambda *_: json_response({}, status=404))))
        assert await plugin._probe_content_length("https://upos/x") is None


class TestDownloadFile:
    async def test_success(self, plugin_factory, tmp_path):
        plugin = plugin_factory(
            {"subscription": {"http_retry_delay_seconds": 0}},
            handler=route(("upos", lambda *_: FakeResponse(chunks=[b"a" * 10, b"", b"b" * 5]))),
        )
        target = tmp_path / "cache" / "v.mp4"
        assert await plugin._download_file("https://upos/x", target, 0) == 15
        assert target.read_bytes() == b"a" * 10 + b"b" * 5

    async def test_oversize_raises_value_error(self, plugin_factory, tmp_path):
        plugin = plugin_factory({}, handler=route(("upos", lambda *_: FakeResponse(chunks=[b"a" * 10]))))
        with pytest.raises(ValueError, match="超过大小限制"):
            await plugin._download_file("https://upos/x", tmp_path / "v.mp4", 5)

    async def test_retryable_error_then_success(self, plugin_factory, tmp_path):
        state = {"n": 0}

        def responder(_method, _url):
            state["n"] += 1
            if state["n"] == 1:
                return aiohttp.ClientConnectionError("reset")
            return FakeResponse(chunks=[b"ok"])

        plugin = plugin_factory(
            {"subscription": {"http_retry_delay_seconds": 0}, "video_download": {"retry_count": 1}},
            handler=route(("upos", responder)),
        )
        target = tmp_path / "v.mp4"
        assert await plugin._download_file("https://upos/x", target, 0) == 2
        assert state["n"] == 2

    async def test_non_retryable_http_error(self, plugin_factory, tmp_path):
        state = {"n": 0}

        def responder(_method, _url):
            state["n"] += 1
            return json_response({}, status=403)

        plugin = plugin_factory(
            {"subscription": {"http_retry_delay_seconds": 0}, "video_download": {"retry_count": 3}},
            handler=route(("upos", responder)),
        )
        with pytest.raises(aiohttp.ClientResponseError):
            await plugin._download_file("https://upos/x", tmp_path / "v.mp4", 0)
        assert state["n"] == 1


class TestDownloadVideo:
    def _handler(self, *, view=VIEW, playurl=PLAYURL, head="10", chunks=None):
        def responder(method, url):
            if "web-interface/view" in url:
                return json_response(view)
            if "player/playurl" in url:
                return json_response(playurl)
            if method == "HEAD":
                return FakeResponse(headers={"Content-Length": head} if head else {})
            return FakeResponse(chunks=[b"x" * 10] if chunks is None else chunks)

        return route(("api.bilibili.com", responder), ("upos", responder))

    async def test_success_writes_file(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"enable": True, "quality": "720P"}}, handler=self._handler())
        path, err = await plugin._download_video({"bvid": BVID, "page": 0})
        assert err == ""
        assert path and path.exists() and path.name.startswith(BVID)
        assert path.read_bytes() == b"x" * 10
        assert path.parent == plugin.video_cache_dir

    async def test_uses_provided_info(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"enable": True}}, handler=self._handler())
        info = dict(VIEW["data"])
        path, err = await plugin._download_video({"bvid": BVID}, info)
        assert path and err == ""
        # 未再次请求 view 接口
        assert not any("web-interface/view" in url for _m, url, _k in plugin._http_session.calls)

    async def test_info_failure(self, plugin_factory):
        plugin = plugin_factory({}, handler=self._handler(view={"code": -404}))
        path, err = await plugin._download_video({"bvid": BVID})
        assert path is None and "获取视频信息失败" in err

    async def test_missing_bvid(self, plugin_factory):
        plugin = plugin_factory({})
        path, err = await plugin._download_video({}, {"title": "x"})
        assert path is None and err == "无法识别 bvid"

    async def test_missing_cid(self, plugin_factory):
        plugin = plugin_factory({})
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"duration": 5}]})
        assert path is None and err == "无法识别 cid"

    async def test_duration_limit(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"max_duration_seconds": 60}})
        info = {"bvid": "BV1", "pages": [{"cid": 1, "duration": 600}]}
        path, err = await plugin._download_video({}, info)
        assert path is None and "时长超过限制" in err

    async def test_no_stream(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"quality": "720P"}},
            handler=self._handler(playurl={"code": -404}),
        )
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path is None and "未获取到可下载的视频流" in err
        # 720P 会逐级降级尝试 720P/480P/360P
        assert len([1 for _m, url, _k in plugin._http_session.calls if "playurl" in url]) == 3

    async def test_quality_downgrade(self, plugin_factory):
        state = {"n": 0}

        def responder(method, url):
            if "playurl" in url:
                state["n"] += 1
                if state["n"] == 1:
                    return json_response({"code": -404})
                return json_response(PLAYURL)
            if method == "HEAD":
                return FakeResponse(headers={"Content-Length": "10"})
            return FakeResponse(chunks=[b"x"])

        plugin = plugin_factory({"video_download": {"quality": "4K"}}, handler=route(("api", responder)))
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path and err == ""
        assert state["n"] == 2

    async def test_empty_stream_url(self, plugin_factory, monkeypatch):
        plugin = plugin_factory({})

        async def fake_playurl(**_kwargs):
            return {"url": "   "}

        monkeypatch.setattr(plugin, "_fetch_playurl", fake_playurl)
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path is None and err == "视频流地址为空"

    async def test_size_limit_by_probe(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"max_size_mb": 1}},
            handler=self._handler(head=str(5 * 1024 * 1024)),
        )
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path is None and "大小超过限制" in err

    async def test_size_limit_without_probe(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"max_size_mb": 1}},
            handler=self._handler(head=""),
        )
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path and err == ""

    async def test_oversize_during_download(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"max_size_mb": 1}},
            handler=self._handler(head="", chunks=[b"y" * (2 * 1024 * 1024)]),
        )
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path is None and "超过大小限制" in err

    async def test_empty_download(self, plugin_factory):
        plugin = plugin_factory({"video_download": {}}, handler=self._handler(head="", chunks=[]))
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path is None and err == "下载结果为空"

    async def test_download_exception(self, plugin_factory):
        def responder(method, url):
            if "playurl" in url:
                return json_response(PLAYURL)
            if method == "HEAD":
                return FakeResponse(headers={})
            return json_response({}, status=403)

        plugin = plugin_factory(
            {"video_download": {"retry_count": 0}},
            handler=route(("api", responder), ("upos", responder)),
        )
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path is None and "下载失败" in err

    async def test_no_size_limit(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"max_size_mb": 0}}, handler=self._handler(head=""))
        path, err = await plugin._download_video({}, {"bvid": "BV1", "pages": [{"cid": 1}]})
        assert path and err == ""

    async def test_second_page_naming(self, plugin_factory):
        view = {"code": 0, "data": {"bvid": "BV1", "pages": [{"cid": 1}, {"cid": 2}]}}
        plugin = plugin_factory({"video_download": {}}, handler=self._handler(view=view))
        path, _err = await plugin._download_video({"bvid": "BV1", "page": 1}, view["data"])
        assert path and "_p2_" in path.name


class TestSendVideoDownload:
    async def test_disabled(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent()
        await plugin._send_video_download(event, {"bvid": "BV1"})
        assert plugin.context.sent == []

    async def test_success_and_auto_delete(self, plugin_factory, tmp_path):
        plugin = plugin_factory(
            {"video_download": {"enable": True}},
            handler=TestDownloadVideo()._handler(),
        )
        event = FakeEvent()
        await plugin._send_video_download(event, {"bvid": BVID})
        assert len(plugin.context.sent) == 1
        _session, chain = plugin.context.sent[0]
        assert isinstance(chain[0], Comp.Video)
        assert not any(plugin.video_cache_dir.glob("*.mp4"))

    async def test_keeps_file_when_auto_delete_off(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"enable": True, "auto_delete": False}},
            handler=TestDownloadVideo()._handler(),
        )
        event = FakeEvent()
        await plugin._send_video_download(event, {"bvid": BVID})
        assert len(list(plugin.video_cache_dir.glob("*.mp4"))) == 1

    async def test_error_message_is_sent(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"enable": True}},
            handler=TestDownloadVideo()._handler(playurl={"code": -404}),
        )
        event = FakeEvent()
        await plugin._send_video_download(event, {"bvid": BVID})
        texts = plugin.context.sent_texts()
        assert len(texts) == 1
        assert "未获取到可下载的视频流" in texts[0]

    async def test_download_exception_is_reported(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"enable": True}})

        async def boom(*_args, **_kwargs):
            raise aiohttp.ClientConnectionError("nope")

        plugin._download_video = boom  # type: ignore[assignment]
        event = FakeEvent()
        await plugin._send_video_download(event, {"bvid": "BV1"})
        assert "视频发送失败" in plugin.context.sent_texts()[0]

    async def test_empty_error_sends_nothing(self, plugin_factory):
        plugin = plugin_factory({"video_download": {"enable": True}})

        async def empty(*_args, **_kwargs):
            return None, ""

        plugin._download_video = empty  # type: ignore[assignment]
        event = FakeEvent()
        await plugin._send_video_download(event, {"bvid": "BV1"})
        assert plugin.context.sent == []


class TestSafeSend:
    async def test_success(self, plugin_factory):
        plugin = plugin_factory({})
        assert await plugin._safe_send("s", [Comp.Plain("x")], "t") is True
        assert plugin.context.sent_texts() == ["x"]

    async def test_failure_is_logged(self, plugin_factory):
        from conftest import FakeContext

        plugin = plugin_factory({}, context=FakeContext(fail_send=True))
        assert await plugin._safe_send("s", [Comp.Plain("x")], "t") is False
