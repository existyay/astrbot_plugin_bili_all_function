"""测试公共设施：接口假实现、事件/上下文替身与夹具加载。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # pragma: no cover - 仅在直接运行 pytest 时生效
    sys.path.insert(0, str(ROOT))

import astrbot.api.message_components as Comp  # noqa: E402
from astrbot.core.message.message_event_result import MessageEventResult  # noqa: E402

import main as plugin_main  # noqa: E402
from bili_models import Subscription  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


# ───────────────────────── 夹具 ─────────────────────────


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def feed_items(name: str) -> list[dict[str, Any]]:
    """加载动态快照并复制一份（避免测试间共享可变状态）。"""
    return json.loads(json.dumps(load_fixture(name)["data"]["items"], ensure_ascii=False))


def deep_copy(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False))


# ───────────────────────── 消息链断言辅助 ─────────────────────────


def chain_texts(chain: Iterable[Any]) -> list[str]:
    return [c.text for c in chain if isinstance(c, Comp.Plain)]


def chain_text(chain: Iterable[Any]) -> str:
    return "\n".join(chain_texts(chain))


def count_images(chain: Iterable[Any]) -> int:
    return sum(1 for c in chain if isinstance(c, Comp.Image))


def result_text(result: MessageEventResult) -> str:
    return result.get_plain_text()


# ───────────────────────── HTTP 假实现 ─────────────────────────


class FakeContent:
    def __init__(self, chunks: list[bytes], error: BaseException | None = None) -> None:
        self._chunks = chunks
        self._error = error

    async def iter_chunked(self, _size: int):
        if self._error is not None:
            raise self._error
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    """模拟 aiohttp 响应（支持 ``async with``、``read`` 与流式下载）。"""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        url: str = "https://api.bilibili.com/x/test",
        chunks: list[bytes] | None = None,
        reason: str = "",
        content_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.reason = reason
        self.request_info = SimpleNamespace(real_url=url)
        self.history: tuple[Any, ...] = ()
        self.content = FakeContent(
            chunks if chunks is not None else ([body] if body else []),
            error=content_error,
        )
        self.closed = False

    async def read(self) -> bytes:
        return self._body

    async def text(self) -> str:
        return self._body.decode("utf-8", errors="ignore")

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _RequestCM:
    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory

    async def __aenter__(self) -> Any:
        result = self._factory()
        if isinstance(result, BaseException):
            raise result
        return result

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class FakeSession:
    """按 handler 路由的 aiohttp.ClientSession 替身。"""

    def __init__(self, handler: Callable[..., Any] | None = None) -> None:
        self.handler = handler or (lambda *a, **k: json_response({"code": 0, "data": {}}))
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def _dispatch(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        return self.handler(method, url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> _RequestCM:
        return _RequestCM(lambda: self._dispatch(method, url, **kwargs))

    def get(self, url: str, **kwargs: Any) -> _RequestCM:
        return _RequestCM(lambda: self._dispatch("GET", url, **kwargs))

    def head(self, url: str, **kwargs: Any) -> _RequestCM:
        return _RequestCM(lambda: self._dispatch("HEAD", url, **kwargs))

    async def close(self) -> None:
        self.closed = True


def json_response(
    payload: Any,
    *,
    status: int = 200,
    url: str = "https://api.bilibili.com/x/test",
    headers: dict[str, str] | None = None,
) -> FakeResponse:
    return FakeResponse(
        status=status,
        body=json.dumps(payload, ensure_ascii=False).encode(),
        headers=headers,
        url=url,
    )


def route(
    *rules: tuple[str, Callable[[str, str], Any]],
    default: Callable[[str, str], Any] | None = None,
) -> Callable[..., Any]:
    """按 URL 子串匹配的请求路由。"""

    def handler(method: str, url: str, **_kwargs: Any) -> Any:
        for token, responder in rules:
            if token in url:
                return responder(method, url)
        if default is not None:
            return default(method, url)
        return json_response({"code": 0, "data": {}})

    return handler


def always(payload: Any) -> Callable[[str, str], Any]:
    return lambda _method, _url: json_response(payload)


def raising(exc: BaseException) -> Callable[[str, str], Any]:
    """构造一个总是抛出 *exc* 的路由处理器（用于异常路径测试）。"""

    def _handler(_method: str, _url: str) -> Any:
        raise exc

    return _handler


# ───────────────────────── AstrBot 事件 / 上下文替身 ─────────────────────────


class FakeEvent:
    def __init__(
        self,
        message_str: str = "",
        *,
        sender_id: str = "10001",
        self_id: str = "9999",
        umo: str = "aiocqhttp:GroupMessage:12345",
        messages: list[Any] | None = None,
        admin: bool = False,
    ) -> None:
        self.message_str = message_str
        self.unified_msg_origin = umo
        self.message_obj = SimpleNamespace(message=list(messages or []))
        self._sender_id = sender_id
        self._self_id = self_id
        self._admin = admin
        self.stopped = False
        self.results: list[MessageEventResult] = []

    # 事件 API
    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return self._self_id

    def get_group_id(self) -> str:
        return "12345"

    def get_session_id(self) -> str:
        return "12345"

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def is_admin(self) -> bool:
        return self._admin

    def stop_event(self) -> None:
        self.stopped = True

    def plain_result(self, text: str) -> MessageEventResult:
        result = MessageEventResult().message(text)
        self.results.append(result)
        return result

    def chain_result(self, chain: list[Any]) -> MessageEventResult:
        result = MessageEventResult()
        result.chain = list(chain)
        self.results.append(result)
        return result


class FakeContext:
    """记录主动发送内容的 Context 替身。"""

    def __init__(self, config: dict[str, Any] | None = None, *, fail_send: bool = False) -> None:
        self.config = config or {}
        self.sent: list[tuple[str, list[Any]]] = []
        self.fail_send = fail_send
        # 插件页面 Web API 注册记录
        self.web_apis: list[tuple[str, Any, list[str], str]] = []
        self.fail_web_api = False

    async def send_message(self, session: Any, chain: Any) -> bool:
        if self.fail_send:
            raise RuntimeError("platform exploded")
        self.sent.append((str(session), list(getattr(chain, "chain", []))))
        return True

    def get_config(self, _umo: str | None = None) -> dict[str, Any]:
        return self.config

    def register_web_api(
        self,
        route: str,
        view_handler: Any,
        methods: list[str],
        desc: str,
    ) -> None:
        if self.fail_web_api:
            raise RuntimeError("route already taken")
        self.web_apis.append((route, view_handler, list(methods), desc))

    def sent_texts(self) -> list[str]:
        return [chain_text(chain) for _session, chain in self.sent]


@pytest.fixture
def plugin_factory(tmp_path, monkeypatch):
    """构造插件实例：数据目录落到 tmp、HTTP 会话替换为假实现。"""
    created: list[plugin_main.BiliAllPlugin] = []

    def make(
        config: dict[str, Any] | None = None,
        *,
        handler: Callable[..., Any] | None = None,
        context: FakeContext | None = None,
        data_dir: Path | None = None,
    ) -> plugin_main.BiliAllPlugin:
        monkeypatch.setenv("ASTRBOT_ROOT", str(data_dir or tmp_path))
        ctx = context if context is not None else FakeContext()
        plugin = plugin_main.BiliAllPlugin(context=ctx, config=config or {})
        plugin.context = ctx
        plugin._http_session = FakeSession(handler)
        # 测试中禁用请求节流，避免额外 sleep 影响确定性断言。
        plugin.min_request_interval_seconds = 0.0
        created.append(plugin)
        return plugin

    yield make
    for plugin in created:
        session = plugin._http_session
        if isinstance(session, FakeSession):
            session.closed = True


@pytest.fixture
def make_sub():
    def _make(uid: int = 2, **kwargs: Any) -> Subscription:
        return Subscription(uid=uid, **kwargs)

    return _make


async def collect(agen) -> list[Any]:
    """收集异步生成器（指令 handler）的全部产出。"""
    return [item async for item in agen]
