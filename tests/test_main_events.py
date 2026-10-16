"""消息事件解析与 LLM 函数工具测试。"""

from __future__ import annotations

from astrbot.api.message_components import Json, Plain
from conftest import FakeEvent, FakeResponse, chain_text, json_response, load_fixture, route

import bili_constants as C

VIEW = load_fixture("video_view.json")
BVID = VIEW["data"]["bvid"]
SHORT_TARGET = f"https://www.bilibili.com/video/{BVID}"


def video_handler(*, view=None, short_url=SHORT_TARGET):
    def responder(_method, url):
        if "web-interface/view" in url:
            return json_response(view or VIEW)
        if "b23.tv" in url:
            return FakeResponse(url=short_url)
        return json_response({"code": 0})

    return route(("api.bilibili.com", responder), ("b23.tv", responder))


class TestHandleBiliParse:
    async def test_disabled(self, plugin_factory):
        plugin = plugin_factory({"features": {"enable_bili_parse": False}}, handler=video_handler())
        event = FakeEvent(f"看这个 {SHORT_TARGET}")
        await plugin.handle_bili_parse(event)
        assert event.stopped is False
        assert plugin.context.sent == []

    async def test_ignores_self_message(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        event = FakeEvent(f"{SHORT_TARGET}", sender_id="9999", self_id="9999")
        await plugin.handle_bili_parse(event)
        assert event.stopped is False
        assert plugin.context.sent == []

    async def test_text_link_sends_video_info(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        event = FakeEvent(f"看看 {SHORT_TARGET} 吧")
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert len(plugin.context.sent) == 1
        session, chain = plugin.context.sent[0]
        assert session == event.unified_msg_origin
        text = chain_text(chain)
        assert VIEW["data"]["title"] in text
        assert BVID in text

    async def test_av_link(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        event = FakeEvent("av123456")
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert "aid=123456" in plugin._http_session.calls[0][1]

    async def test_short_link_resolution(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        event = FakeEvent("https://b23.tv/abc123")
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert BVID in chain_text(plugin.context.sent[0][1])

    async def test_no_bili_link(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        event = FakeEvent("今天天气不错 https://example.com/video")
        await plugin.handle_bili_parse(event)
        assert event.stopped is False
        assert plugin.context.sent == []

    async def test_bili_link_without_video_id(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=video_handler(short_url="https://space.bilibili.com/123/dynamic"),
        )
        event = FakeEvent("https://b23.tv/no-video-here")
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert plugin.context.sent == []

    async def test_json_card(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        card = Json(data={"meta": {"detail_1": {"qqdocurl": SHORT_TARGET}}})
        event = FakeEvent("", messages=[card])
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert VIEW["data"]["title"] in chain_text(plugin.context.sent[0][1])

    async def test_json_card_unresolvable(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        card = Json(data={"meta": {"detail_1": {"qqdocurl": "https://example.com/x"}}})
        event = FakeEvent("", messages=[card])
        await plugin.handle_bili_parse(event)
        assert event.stopped is False
        assert plugin.context.sent == []

    async def test_json_card_with_bilibili_url_but_no_id(self, plugin_factory):
        plugin = plugin_factory(
            {},
            handler=video_handler(short_url="https://space.bilibili.com/123/dynamic"),
        )
        card = Json(data={"url": "https://b23.tv/nothing"})
        event = FakeEvent("", messages=[card])
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert plugin.context.sent == []

    async def test_text_link_wins_over_json(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        card = Json(data={"url": "https://www.bilibili.com/video/BV1zz411c7mD"})
        event = FakeEvent(f"看 {SHORT_TARGET}", messages=[card])
        await plugin.handle_bili_parse(event)
        assert BVID in chain_text(plugin.context.sent[0][1])

    async def test_send_video_info_disabled(self, plugin_factory):
        plugin = plugin_factory({"features": {"send_video_info": False}}, handler=video_handler())
        event = FakeEvent(f"看 {SHORT_TARGET}")
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert plugin.context.sent == []
        assert plugin._http_session.calls == []

    async def test_info_fetch_failure(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler(view={"code": -404}))
        event = FakeEvent(f"看 {SHORT_TARGET}")
        await plugin.handle_bili_parse(event)
        assert plugin.context.sent == []

    async def test_download_enabled(self, plugin_factory):
        plugin = plugin_factory(
            {"video_download": {"enable": True}},
            handler=route(
                ("web-interface/view", lambda *_: json_response(VIEW)),
                ("player/playurl", lambda *_: json_response(load_fixture("playurl.json"))),
                ("bilivideo", lambda *_: FakeResponse(chunks=[b"z" * 8])),
            ),
        )
        event = FakeEvent(f"看 {SHORT_TARGET}")
        await plugin.handle_bili_parse(event)
        texts = plugin.context.sent_texts()
        assert any(VIEW["data"]["title"] in t for t in texts)
        from astrbot.api.message_components import Video

        assert any(isinstance(c, Video) for _s, chain in plugin.context.sent for c in chain)

    async def test_send_failure_is_tolerated(self, plugin_factory):
        from conftest import FakeContext

        plugin = plugin_factory({}, context=FakeContext(fail_send=True), handler=video_handler())
        event = FakeEvent(f"看 {SHORT_TARGET}")
        await plugin.handle_bili_parse(event)
        assert event.stopped is True
        assert plugin.context.sent == []

    async def test_json_links_event_without_message(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent("BV1GJ411x7h7")
        del event.message_obj
        await plugin.handle_bili_parse(event)
        assert plugin._coerce_json({"a": {"b": 1}}) == {"a": {"b": 1}}


class TestLlmTool:
    async def test_ok(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        event = FakeEvent("")
        result = await plugin.llm_bili_video_info(event, f"https://www.bilibili.com/video/{BVID}")
        assert VIEW["data"]["title"] in result
        assert BVID in result

    async def test_bare_bv(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler())
        assert BVID in await plugin.llm_bili_video_info(FakeEvent(""), BVID)

    async def test_disabled(self, plugin_factory):
        plugin = plugin_factory({"features": {"enable_llm_tool": False}})
        result = await plugin.llm_bili_video_info(FakeEvent(""), BVID)
        assert "已被插件配置关闭" in result

    async def test_unrecognized(self, plugin_factory):
        plugin = plugin_factory({})
        result = await plugin.llm_bili_video_info(FakeEvent(""), "随便一段文字")
        assert "未能识别" in result

    async def test_empty_url(self, plugin_factory):
        plugin = plugin_factory({})
        assert "未能识别" in await plugin.llm_bili_video_info(FakeEvent(""), "")

    async def test_info_failure(self, plugin_factory):
        plugin = plugin_factory({}, handler=video_handler(view={"code": -404}))
        assert "视频信息获取失败" in await plugin.llm_bili_video_info(FakeEvent(""), BVID)

    def test_tool_is_registered_with_schema(self):
        """LLM 工具在导入期注册，且参数 schema 与文档一致。"""
        from astrbot.core.star.register import star_handler

        tool = star_handler.llm_tools.get_func("bili_video_info")
        assert tool is not None, "LLM 工具 bili_video_info 未注册"
        assert tool.parameters["type"] == "object"
        assert set(tool.parameters["properties"]) == {"url"}
        assert tool.parameters["properties"]["url"]["type"] == "string"
        assert "B 站视频" in tool.description
        assert C.PLUGIN_VERSION


class TestJsonCardHelpers:
    def test_extract_from_plain_component(self, plugin_factory):
        plugin = plugin_factory({})
        event = FakeEvent("", messages=[Plain("https://b23.tv/plain-not-json")])
        assert plugin._extract_json_links_from_event(event) == []
