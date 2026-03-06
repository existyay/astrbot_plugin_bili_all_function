"""AstrBot B站全功能插件：链接解析 + UP 主动态订阅 + 直播推送。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, StarTools, register

try:
    from .bili_constants import (
        QUALITY_TO_QN, RE_AV, RE_BV, RE_MESSAGE, RE_SHORT_LINK, RE_URL,
        VALID_FILTER_TYPES,
    )
    from .bili_helpers import (
        extract_links_and_ids, is_top_dynamic, normalize_modules,
        parse_filter_args, parse_page_index, resolve_video_page,
    )
    from .bili_logic import (
        append_recent_id, build_dynamic_chain, build_uid_targets,
        build_video_chain, should_filter_dynamic,
    )
    from .bili_models import Subscription
except ImportError:
    from bili_constants import (
        QUALITY_TO_QN, RE_AV, RE_BV, RE_MESSAGE, RE_SHORT_LINK, RE_URL,
        VALID_FILTER_TYPES,
    )
    from bili_helpers import (
        extract_links_and_ids, is_top_dynamic, normalize_modules,
        parse_filter_args, parse_page_index, resolve_video_page,
    )
    from bili_logic import (
        append_recent_id, build_dynamic_chain, build_uid_targets,
        build_video_chain, should_filter_dynamic,
    )
    from bili_models import Subscription

# ── 模块级常量 ──
_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


# ═══════════════════════ 插件主体 ═══════════════════════

@register("astrbot_plugin_bili_all_function", "existyay", "B站链接解析与UP动态订阅", "0.2.0")
class BiliAllPlugin(Star):

    # ───────────────────── 生命周期 ─────────────────────

    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config or context.get_config() or {}
        self._poll_task: asyncio.Task | None = None
        self._running = False

        # 配置默认值（会被 _refresh_config 覆盖）
        self.enable_bili_parse = True
        self.poll_interval_seconds = 300
        self.timeout_seconds = 15
        self.dynamic_limit = 3
        self.uid_task_gap_seconds = 1
        self.recent_ids_limit = 20
        self.admin_ids: set[str] = set()
        self.enable_video_download = False
        self.video_quality_label = "720P"
        self.video_max_duration_seconds = 600
        self.video_max_size_mb = 100
        self.video_auto_delete = True
        self.video_retry_count = 2
        self.bili_cookie = ""
        self.http_retry_count = 2
        self.http_retry_delay_seconds = 0.35
        self._http_session: aiohttp.ClientSession | None = None

        self._refresh_config()

        plugin_data_dir = Path(StarTools.get_data_dir())
        self.data_path = plugin_data_dir / "subscriptions.json"
        self.video_cache_dir = plugin_data_dir / "cache" / "videos"
        self.subscriptions: dict[str, list[Subscription]] = {}

    async def initialize(self):
        self._load_subscriptions()
        await self._ensure_http_session()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="bili-dynamic-poll")
        logger.info("BiliAll 插件初始化完成，订阅数: %d", self._sub_count())

    async def terminate(self):
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None
        self._save_subscriptions()

    # ───────────────────── 配置 ─────────────────────

    @staticmethod
    def _int(value: Any, default: int, *, min_val: int = 1) -> int:
        """安全整数转换，低于 *min_val* 时回退到 *default*。"""
        try:
            v = int(value)
            return v if v >= min_val else default
        except (TypeError, ValueError, OverflowError):
            return default

    def _cfg(self, key: str, default: Any = None) -> Any:
        """按点分路径读取嵌套配置。"""
        node: Any = self.config
        for k in key.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(k)
            if node is None:
                return default
        return node

    def _refresh_config(self) -> None:
        c, ci = self._cfg, self._int
        self.enable_bili_parse = bool(c("features.enable_bili_parse", True))
        self.poll_interval_seconds = ci(c("subscription.poll_interval_seconds", 300), 300)
        self.timeout_seconds = ci(c("subscription.timeout_seconds", 15), 15)
        self.dynamic_limit = ci(c("subscription.dynamic_limit", 3), 3)
        self.uid_task_gap_seconds = ci(c("subscription.uid_task_gap_seconds", 1), 1, min_val=0)
        self.recent_ids_limit = ci(c("subscription.recent_ids_limit", 20), 20)

        self.enable_video_download = bool(c("video_download.enable", False))
        label = str(c("video_download.quality", "720P")).upper()
        self.video_quality_label = label if label in QUALITY_TO_QN else "720P"
        self.video_max_duration_seconds = ci(c("video_download.max_duration_seconds", 600), 600, min_val=0)
        self.video_max_size_mb = ci(c("video_download.max_size_mb", 100), 100, min_val=0)
        self.video_auto_delete = bool(c("video_download.auto_delete", True))
        self.video_retry_count = max(0, ci(c("video_download.retry_count", 2), 2, min_val=0))
        self.bili_cookie = str(c("video_download.bili_cookie", "") or "").strip()
        self.http_retry_count = max(0, ci(c("subscription.http_retry_count", 2), 2, min_val=0))
        try:
            d = float(c("subscription.http_retry_delay_seconds", 0.35))
            self.http_retry_delay_seconds = d if d >= 0 else 0.35
        except (TypeError, ValueError):
            self.http_retry_delay_seconds = 0.35

        raw = c("permission.admin_ids", "")
        if isinstance(raw, list):
            self.admin_ids = {str(x).strip() for x in raw if str(x).strip()}
        elif isinstance(raw, str) and raw.strip():
            self.admin_ids = {p for p in re.split(r"[,\s]+", raw.strip()) if p}
        else:
            self.admin_ids = set()

    # ───────────────────── HTTP 层 ─────────────────────

    def _headers(self, *, referer: bool = False, cookie: bool = False) -> dict[str, str]:
        h: dict[str, str] = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Origin": "https://www.bilibili.com",
        }
        if referer:
            h["Referer"] = "https://www.bilibili.com/"
        if cookie and self.bili_cookie:
            h["Cookie"] = self.bili_cookie
        return h

    @staticmethod
    def _classify_exc(exc: Exception) -> tuple[str, bool]:
        """分类异常 → (标签, 是否可重试)。"""
        if isinstance(exc, aiohttp.ClientResponseError):
            code = int(getattr(exc, "status", 0) or 0)
            tag = "http_client" if 400 <= code < 500 else ("http_server" if code >= 500 else "http")
            return f"{tag}_{code}", code in _RETRYABLE_HTTP
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return "timeout", True
        if isinstance(exc, (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError)):
            return "network", True
        return "unknown", False

    async def _ensure_http_session(self) -> aiohttp.ClientSession:
        if self._http_session and not self._http_session.closed:
            return self._http_session
        self._http_session = aiohttp.ClientSession(trust_env=True)
        return self._http_session

    async def _request(
        self, *, url: str, method: str = "GET",
        headers: dict[str, str], read_body: bool = True,
        scene: str, silent_codes: set[int] | None = None,
        retry: bool = True, retry_count: int | None = None,
    ) -> dict[str, Any] | None:
        session = await self._ensure_http_session()

        effective = retry_count if retry_count is not None else self.http_retry_count
        max_att = (effective + 1) if retry else 1
        for att in range(1, max_att + 1):
            try:
                async with session.request(
                    method, url, headers=headers,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                ) as resp:
                    status = int(resp.status or 0)
                    if silent_codes and status in silent_codes:
                        logger.info("请求静默降级[%s][http_%s]", scene, status)
                        return None
                    if status >= 400:
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=status,
                            message=resp.reason or "",
                            headers=resp.headers,
                        )
                    return {
                        "body": await resp.read() if read_body else b"",
                        "headers": resp.headers,
                        "status": status,
                        "final_url": str(resp.url),
                    }
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
                if isinstance(exc, aiohttp.ClientResponseError):
                    code = int(getattr(exc, "status", 0) or 0)
                    if silent_codes and code in silent_codes:
                        logger.info("请求静默降级[%s][http_%s]", scene, code)
                        return None
                cat, retryable = self._classify_exc(exc)
                logger.warning("请求失败[%s][%s] %d/%d: %s", scene, cat, att, max_att, exc)
                if not retryable or att >= max_att:
                    break
                await asyncio.sleep(self.http_retry_delay_seconds * att)
        return None

    async def _get_json(
        self, base_url: str, params: dict[str, Any], *,
        headers: dict[str, str] | None = None,
        scene: str = "get_json",
        silent_codes: set[int] | None = None,
        retry: bool = True, retry_count: int | None = None,
    ) -> dict[str, Any] | None:
        qs = urlencode(params, doseq=True)
        full = f"{base_url}?{qs}" if qs else base_url
        result = await self._request(
            url=full, headers=headers or self._headers(referer=True, cookie=True),
            scene=scene, silent_codes=silent_codes, retry=retry,
            retry_count=retry_count,
        )
        if not result:
            return None
        try:
            return json.loads(bytes(result["body"]).decode("utf-8", errors="ignore"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("JSON 解析失败[%s]: %s", scene, exc)
            return None

    async def _get_json_candidates(
        self, base_url: str, params: dict[str, Any],
        header_list: list[dict[str, str]], *,
        scene: str, silent_codes: set[int] | None = None,
        retry: bool = True, retry_count: int | None = None,
    ) -> dict[str, Any] | None:
        """尝试多组 headers 直到其中一组返回有效 payload。"""
        for i, h in enumerate(header_list, 1):
            payload = await self._get_json(
                base_url, params, headers=h,
                scene=f"{scene}_{i}", silent_codes=silent_codes, retry=retry,
                retry_count=retry_count,
            )
            if payload is not None:
                return payload
        return None

    # ───────────────────── 订阅持久化 ─────────────────────

    def _sub_count(self) -> int:
        return sum(len(v) for v in self.subscriptions.values())

    def _load_subscriptions(self) -> None:
        if not self.data_path.exists():
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            self.subscriptions = {}
            return
        try:
            raw = json.loads(self.data_path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.error("加载订阅数据失败: %s", exc)
            self.subscriptions = {}
            return

        loaded: dict[str, list[Subscription]] = {}
        for session, items in raw.items():
            if not isinstance(items, list):
                continue
            subs: list[Subscription] = []
            for it in items:
                try:
                    ft_raw = it.get("filter_types") or []
                    fr_raw = it.get("filter_regex") or []
                    ri_raw = it.get("recent_ids") or []
                    subs.append(Subscription(
                        uid=int(it["uid"]),
                        last_dynamic_id=str(it.get("last_dynamic_id") or ""),
                        is_live=bool(it.get("is_live")),
                        filter_types=[s for x in (ft_raw if isinstance(ft_raw, list) else [])
                                      if (s := str(x).strip()) and s in VALID_FILTER_TYPES],
                        filter_regex=[s for x in (fr_raw if isinstance(fr_raw, list) else [])
                                      if (s := str(x).strip())],
                        recent_ids=[str(x) for x in (ri_raw if isinstance(ri_raw, list) else []) if x],
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
            if subs:
                loaded[str(session)] = subs
        self.subscriptions = loaded

    def _save_subscriptions(self) -> None:
        try:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            data = {s: [asdict(sub) for sub in lst] for s, lst in self.subscriptions.items() if lst}
            self.data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            logger.error("保存订阅数据失败: %s", exc)

    def _session_key(self, event: AstrMessageEvent) -> str:
        umo = getattr(event, "unified_msg_origin", None)
        return str(umo) if umo else f"sender:{event.get_sender_id()}"

    # ───────────────────── 链接解析工具 ─────────────────────

    def _coerce_json(self, data: Any, _depth: int = 0) -> dict | None:
        """递归解包 JSON 字符串/嵌套 data 字段，提取最终 dict。"""
        if _depth > 5 or data is None:
            return None
        if isinstance(data, str):
            s = data.strip()
            if not s:
                return None
            try:
                return self._coerce_json(json.loads(s), _depth + 1)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        if isinstance(data, dict):
            if len(data) == 1 and "data" in data:
                return self._coerce_json(data["data"], _depth + 1)
            return data
        if isinstance(data, list):
            for item in data:
                got = self._coerce_json(item, _depth + 1)
                if got:
                    return got
        if hasattr(data, "data"):
            return self._coerce_json(getattr(data, "data"), _depth + 1)
        return None

    def _extract_links_from_json(self, json_data: Any) -> list[str]:
        """从 QQ 小程序 JSON 卡片中提取 URL。"""
        payload = self._coerce_json(json_data)
        if not payload:
            return []

        def walk(obj: Any) -> list[str]:
            found: list[str] = []
            if isinstance(obj, dict):
                for v in obj.values():
                    if isinstance(v, str):
                        found.extend(RE_URL.findall(v))
                    elif isinstance(v, (dict, list)):
                        found.extend(walk(v))
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, str):
                        found.extend(RE_URL.findall(item))
                    elif isinstance(item, (dict, list)):
                        found.extend(walk(item))
            return found

        links = walk(payload)
        # QQ 小程序特定字段快速提取
        meta = payload.get("meta") or {}
        if isinstance(meta, dict):
            for sec in ("detail_1", "news"):
                section = meta.get(sec) or {}
                if isinstance(section, dict):
                    for k in ("qqdocurl", "url", "desc", "jumpUrl"):
                        v = section.get(k)
                        if isinstance(v, str):
                            links.extend(RE_URL.findall(v))
        return list(dict.fromkeys(links))

    def _extract_json_links_from_event(self, event: AstrMessageEvent) -> list[str]:
        """从消息事件中提取 JSON 卡片里的链接。"""
        msg_obj = getattr(event, "message_obj", None)
        if not msg_obj or not hasattr(msg_obj, "message"):
            return []
        links: list[str] = []
        for comp in msg_obj.message:
            if isinstance(comp, dict):
                comp_type = str(comp.get("type") or "").lower()
                if comp_type == "reply":
                    continue
                is_json = "json" in comp_type
                payload = comp.get("data") or comp
            else:
                comp_type = str(getattr(comp, "type", "") or "").lower()
                is_json = isinstance(comp, Comp.Json) or "json" in comp_type
                payload = getattr(comp, "data", comp)
            if is_json:
                links.extend(self._extract_links_from_json(payload))
        return list(dict.fromkeys(links))

    async def _resolve_short_url(self, short_url: str) -> str:
        result = await self._request(
            url=short_url, headers=self._headers(referer=True, cookie=True),
            read_body=False, scene="resolve_short_url",
        )
        return str(result.get("final_url") or short_url) if result else short_url

    async def _extract_video_ref(self, token: str) -> dict[str, Any] | None:
        """从单个链接/ID 提取视频引用信息。"""
        current = token
        if RE_SHORT_LINK.match(token):
            current = await self._resolve_short_url(token)
        m = RE_BV.search(current)
        if m:
            return {"bvid": m.group(0), "aid": None, "source": token, "page": parse_page_index(current)}
        m = RE_AV.search(current)
        if m:
            return {"bvid": None, "aid": int(m.group(0)[2:]), "source": token, "page": parse_page_index(current)}
        return None

    async def _extract_video_ref_from_tokens(self, tokens: list[str]) -> dict[str, Any] | None:
        for token in tokens:
            ref = await self._extract_video_ref(token)
            if ref:
                return ref
        return None

    # ───────────────────── 视频信息 & 下载 ─────────────────────

    async def _get_video_info(
        self, ref: dict[str, Any], *, retry_count: int | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        if ref.get("bvid"):
            params["bvid"] = ref["bvid"]
        elif ref.get("aid"):
            params["aid"] = ref["aid"]
        else:
            return None
        payload = await self._get_json(
            "https://api.bilibili.com/x/web-interface/view", params, scene="video_info",
            retry_count=retry_count,
        )
        return payload.get("data") if payload and payload.get("code") == 0 else None

    @staticmethod
    def _quality_candidates(label: str) -> list[int]:
        selected = QUALITY_TO_QN.get(label.upper(), 64)
        levels = sorted(set(QUALITY_TO_QN.values()), reverse=True)
        idx = levels.index(selected) if selected in levels else levels.index(64)
        return levels[idx:]

    async def _fetch_playurl(self, *, bvid: str, cid: int, qn: int) -> dict[str, Any] | None:
        payload = await self._get_json(
            "https://api.bilibili.com/x/player/playurl",
            {"bvid": bvid, "cid": cid, "qn": qn, "fnval": 0, "fnver": 0, "fourk": 1},
            scene="playurl", retry_count=self.video_retry_count,
        )
        if not payload or payload.get("code") != 0:
            return None
        data = payload.get("data") or {}
        # durl 模式
        durl = data.get("durl") or []
        if isinstance(durl, list) and durl and isinstance(durl[0], dict):
            url = str(durl[0].get("url") or "").strip()
            if url:
                return {"url": url, "quality": int(data.get("quality") or qn)}
        # dash 模式
        dash = data.get("dash")
        videos = dash.get("video") if isinstance(dash, dict) else None
        if isinstance(videos, list) and videos and isinstance(videos[0], dict):
            url = str(videos[0].get("baseUrl") or videos[0].get("base_url") or "").strip()
            if url:
                return {"url": url, "quality": int(videos[0].get("id") or qn)}
        return None

    async def _probe_content_length(self, url: str) -> int | None:
        result = await self._request(
            url=url, method="HEAD", headers=self._headers(referer=True, cookie=True),
            read_body=False, scene="probe_size",
            retry_count=self.video_retry_count,
        )
        if not result:
            return None
        try:
            cl = (result.get("headers") or {}).get("Content-Length")
            return int(cl) if cl else None
        except (TypeError, ValueError, AttributeError):
            return None

    async def _download_file(self, url: str, save_path: Path, max_bytes: int) -> int:
        session = await self._ensure_http_session()

        max_att = self.video_retry_count + 1
        for att in range(1, max_att + 1):
            try:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                total = 0
                async with session.get(
                    url,
                    headers=self._headers(referer=True, cookie=True),
                    timeout=aiohttp.ClientTimeout(sock_read=self.timeout_seconds),
                ) as resp:
                    status = int(resp.status or 0)
                    if status >= 400:
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=status,
                            message=resp.reason or "",
                            headers=resp.headers,
                        )
                    with save_path.open("wb") as f:
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if max_bytes > 0 and total > max_bytes:
                                raise ValueError("视频超过大小限制")
                            f.write(chunk)
                return total
            except ValueError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError) as exc:
                cat, retryable = self._classify_exc(exc)
                logger.warning("下载失败[%s] %d/%d: %s", cat, att, max_att, exc)
                try:
                    save_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if not retryable or att >= max_att:
                    raise
                await asyncio.sleep(self.http_retry_delay_seconds * att)
        raise RuntimeError("下载失败")

    async def _download_video(
        self, ref: dict[str, Any], info: dict[str, Any] | None = None,
    ) -> tuple[Path | None, str]:
        """下载视频文件，返回 (保存路径, 错误信息)。"""
        if info is None:
            info = await self._get_video_info(ref, retry_count=self.video_retry_count)
        if not info:
            return None, "获取视频信息失败"

        bvid = str(info.get("bvid") or ref.get("bvid") or "").strip()
        if not bvid:
            return None, "无法识别 bvid"

        page, page_idx, _ = resolve_video_page(info, ref)
        cid = int(page.get("cid") or 0)
        if cid <= 0:
            return None, "无法识别 cid"

        duration = int(page.get("duration") or info.get("duration") or 0)
        if self.video_max_duration_seconds > 0 and duration > self.video_max_duration_seconds:
            return None, f"视频时长超过限制({duration}s > {self.video_max_duration_seconds}s)"

        stream: dict[str, Any] | None = None
        for qn in self._quality_candidates(self.video_quality_label):
            stream = await self._fetch_playurl(bvid=bvid, cid=cid, qn=qn)
            if stream:
                break
        if not stream:
            return None, "未获取到可下载的视频流"
        stream_url = str(stream.get("url") or "").strip()
        if not stream_url:
            return None, "视频流地址为空"

        max_bytes = self.video_max_size_mb * 1024 * 1024 if self.video_max_size_mb > 0 else 0
        if max_bytes > 0:
            cl = await self._probe_content_length(stream_url)
            if cl and cl > max_bytes:
                return None, f"视频大小超过限制({cl / 1024 / 1024:.1f}MB > {self.video_max_size_mb}MB)"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = self.video_cache_dir / f"{bvid}_p{page_idx + 1}_{ts}.mp4"
        try:
            size = await self._download_file(stream_url, save_path, max_bytes)
            if size <= 0 or not save_path.exists():
                return None, "下载结果为空"
            return save_path, ""
        except ValueError as exc:
            save_path.unlink(missing_ok=True)
            return None, str(exc)
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError, RuntimeError) as exc:
            save_path.unlink(missing_ok=True)
            return None, f"下载失败: {exc}"

    async def _send_video_download(
        self, event: AstrMessageEvent, ref: dict[str, Any],
        info: dict[str, Any] | None = None,
    ) -> None:
        """可选的视频下载并发送（仅 enable_video_download=True 时生效）。"""
        if not self.enable_video_download:
            return
        video_path: Path | None = None
        try:
            video_path, err = await self._download_video(ref, info)
            if not video_path:
                if err:
                    await self.context.send_message(
                        event.unified_msg_origin,
                        MessageEventResult(chain=[Comp.Plain(f"[Warning] {err}")]),
                    )
                return
            await self.context.send_message(
                event.unified_msg_origin,
                MessageEventResult(chain=[
                    Comp.Video.fromFileSystem(str(video_path.resolve())),
                ]),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError, RuntimeError) as exc:
            logger.warning("发送下载视频失败: %s", exc)
            await self.context.send_message(
                event.unified_msg_origin,
                MessageEventResult(chain=[Comp.Plain(f"[Warning] 视频发送失败: {exc}")]),
            )
        finally:
            if self.video_auto_delete and video_path and video_path.exists():
                try:
                    await asyncio.to_thread(video_path.unlink, True)
                except (OSError, RuntimeError):
                    pass

    # ───────────────────── 动态 & 直播 ─────────────────────

    async def _fetch_dynamic_items(self, uid: int) -> list[dict[str, Any]]:
        payload = await self._get_json_candidates(
            "https://api.bilibili.com/x/polymer/web-dynamic/desktop/v1/feed/space",
            {"host_mid": uid, "timezone_offset": -480},
            [self._headers(referer=True), self._headers(referer=True, cookie=True)],
            scene="fetch_dynamic", silent_codes={412}, retry=False,
        )
        if not payload or payload.get("code") != 0:
            return []
        items = [
            it for it in (payload.get("data", {}).get("items") or [])
            if isinstance(it, dict) and it.get("id_str")
        ]
        for it in items:
            normalize_modules(it)
        return items

    async def _fetch_live_status(self, uid: int) -> dict[str, Any] | None:
        payload = await self._get_json(
            "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids",
            {"uids[]": uid}, scene="live_status", retry=False,
        )
        if payload and payload.get("code") == 0:
            data = payload.get("data") or {}
            if isinstance(data, dict):
                row = data.get(str(uid))
                if isinstance(row, dict):
                    return row
        # 兜底
        return await self._fetch_live_room(uid)

    async def _fetch_live_room(self, uid: int) -> dict[str, Any] | None:
        payload = await self._get_json(
            "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld",
            {"mid": uid}, scene="live_room", retry=False,
        )
        if not payload or payload.get("code") != 0:
            return None
        d = payload.get("data") or {}
        if not isinstance(d, dict):
            return None
        return {
            "uid": uid,
            "uname": d.get("uname") or f"UID {uid}",
            "room_id": d.get("roomid") or 0,
            "title": d.get("title") or "",
            "live_status": int(d.get("liveStatus") or 0),
        }

    # ───────────────────── 订阅处理 ─────────────────────

    def _upsert_subscription(
        self, session: str, uid: int,
        filter_types: list[str], filter_regex: list[str],
        latest_dyn_id: str = "",
    ) -> tuple[Subscription, bool]:
        subs = self.subscriptions.setdefault(session, [])
        for sub in subs:
            if sub.uid == uid:
                sub.filter_types = filter_types
                sub.filter_regex = filter_regex
                return sub, True
        new = Subscription(
            uid=uid, last_dynamic_id=latest_dyn_id,
            filter_types=filter_types, filter_regex=filter_regex,
        )
        subs.append(new)
        return new, False

    async def _process_sub(
        self, session: str, sub: Subscription,
        dyn_items: list[dict[str, Any]], live_info: dict[str, Any] | None,
    ) -> bool:
        changed = False
        known = {sub.last_dynamic_id, *(sub.recent_ids or [])} - {""}

        new_items: list[dict[str, Any]] = []
        for item in dyn_items:
            if is_top_dynamic(item):
                continue
            dyn_id = str(item.get("id_str") or "")
            if not dyn_id or dyn_id in known:
                break
            new_items.append(item)

        sent = 0
        for item in reversed(new_items):
            dyn_id = str(item.get("id_str") or "")
            if not dyn_id:
                continue
            if not should_filter_dynamic(item, sub) and sent < self.dynamic_limit:
                await self.context.send_message(
                    session, MessageEventResult(chain=build_dynamic_chain(item)),
                )
                sent += 1
            sub.last_dynamic_id = dyn_id
            append_recent_id(sub, dyn_id, self.recent_ids_limit)
            changed = True

        # 直播状态变更
        if live_info and "live" not in (sub.filter_types or []):
            now_live = int(live_info.get("live_status") or 0) == 1
            if now_live != sub.is_live:
                sub.is_live = now_live
                changed = True
                uname = live_info.get("uname") or f"UID {sub.uid}"
                if now_live:
                    title = live_info.get("title") or ""
                    room = live_info.get("room_id") or ""
                    link = f"https://live.bilibili.com/{room}" if room else ""
                    msg = f"[LIVE] {uname} 开播了\n{title}".strip()
                    if link:
                        msg += f"\n{link}"
                else:
                    msg = f"[OFFLINE] {uname} 下播了"
                await self.context.send_message(session, MessageEventResult().message(msg))
        return changed

    async def _poll_loop(self):
        while self._running:
            try:
                t0 = time.monotonic()
                changed = False
                uid_targets = build_uid_targets(self.subscriptions)
                for uid, targets in uid_targets.items():
                    dyn_res, live_res = await asyncio.gather(
                        self._fetch_dynamic_items(uid),
                        self._fetch_live_status(uid),
                        return_exceptions=True,
                    )
                    dyn_items = dyn_res if isinstance(dyn_res, list) else []
                    live = live_res if isinstance(live_res, dict) else None
                    if isinstance(dyn_res, Exception):
                        logger.warning("拉取动态失败 uid=%s: %s", uid, dyn_res)
                    if isinstance(live_res, Exception):
                        logger.warning("拉取直播状态失败 uid=%s: %s", uid, live_res)
                    for sess, sub in targets:
                        if await self._process_sub(sess, sub, dyn_items, live):
                            changed = True
                    if self.uid_task_gap_seconds > 0:
                        await asyncio.sleep(self.uid_task_gap_seconds)

                if changed:
                    self._save_subscriptions()
                await asyncio.sleep(max(0.0, self.poll_interval_seconds - (time.monotonic() - t0)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("订阅轮询异常: %s", exc)
                await asyncio.sleep(5)

    # ───────────────────── 状态自检 ─────────────────────

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return str(event.get_sender_id()) in self.admin_ids

    def _validate_config(self) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if self.video_quality_label not in QUALITY_TO_QN:
            errors.append(f"video_download.quality 非法: {self.video_quality_label}")
        for attr, label in (
            ("poll_interval_seconds", "subscription.poll_interval_seconds"),
            ("timeout_seconds", "subscription.timeout_seconds"),
            ("dynamic_limit", "subscription.dynamic_limit"),
        ):
            if getattr(self, attr, 0) <= 0:
                errors.append(f"{label} 必须 > 0")
        for attr, label in (
            ("video_max_duration_seconds", "video_download.max_duration_seconds"),
            ("video_max_size_mb", "video_download.max_size_mb"),
        ):
            if getattr(self, attr, -1) < 0:
                errors.append(f"{label} 必须 >= 0（0 表示不限制）")
        qn = QUALITY_TO_QN.get(self.video_quality_label, 64)
        if self.enable_video_download and qn >= 80 and not self.bili_cookie:
            warnings.append("已启用高画质下载但未填写 Cookie，可能无法获取 1080P 及以上")
        if self.bili_cookie and "SESSDATA=" not in self.bili_cookie:
            warnings.append("Cookie 中未检测到 SESSDATA，登录态可能无效")
        return errors, warnings

    async def _check_cookie(self) -> tuple[str, str]:
        payload = await self._get_json(
            "https://api.bilibili.com/x/web-interface/nav", {}, scene="nav",
        )
        if not payload:
            return "fail", "请求 nav 接口失败（网络或超时）"
        if payload.get("code") != 0:
            return "fail", f"nav 接口返回错误 code={payload.get('code')}"
        data = payload.get("data") or {}
        logged_in = bool(data.get("isLogin"))
        uname = str(data.get("uname") or "")
        if self.bili_cookie:
            if logged_in:
                return "ok", f"Cookie 有效，当前登录账号: {uname or '未知'}"
            return "fail", "已填写 Cookie 但未登录，Cookie 可能失效"
        if logged_in:
            return "warn", f"未配置 Cookie，但环境已有登录态: {uname or '未知'}"
        return "warn", "未配置 Cookie，下载画质可能受限"

    async def _check_subscription(self, session: str) -> tuple[str, str]:
        total = self._sub_count()
        loop_ok = bool(self._running and self._poll_task and not self._poll_task.done())
        loop_msg = "轮询任务运行中" if loop_ok else "轮询任务未运行"

        test_uid: int | None = None
        for subs in (self.subscriptions.get(session, []),
                     *(self.subscriptions.values() if self.subscriptions else [])):
            if subs:
                test_uid = subs[0].uid
                break
        if not test_uid:
            return "warn", f"当前无订阅数据（sessions={len(self.subscriptions)}, subs={total}，{loop_msg}）"

        items = await self._fetch_dynamic_items(test_uid)
        live = await self._fetch_live_status(test_uid)
        if not items and live is None:
            return "fail", f"订阅拉取失败：UID {test_uid} 的动态与直播接口均无响应"
        return (
            "ok" if loop_ok else "warn",
            f"订阅可用：UID {test_uid} 动态{len(items)}条，直播{'可用' if live else '不可用'}（{loop_msg}）",
        )

    # ───────────────────── 指令：公共辅助 ─────────────────────

    @staticmethod
    def _parse_uid_args(args: list[str], uid_pos: int = 1) -> tuple[int, list[str], list[str]] | None:
        """从命令参数提取 (uid, filter_types, filter_regex)，失败返回 None。"""
        uid_str = args[uid_pos] if len(args) > uid_pos else ""
        if not uid_str.isdigit():
            return None
        ft, fr = parse_filter_args(args[uid_pos + 1:], VALID_FILTER_TYPES)
        return int(uid_str), ft, fr

    @staticmethod
    def _fmt_filter(ft: list[str], fr: list[str]) -> str:
        parts: list[str] = []
        if ft:
            parts.append("类型过滤: " + ", ".join(ft))
        if fr:
            parts.append("正则过滤: " + ", ".join(fr))
        return ("\n" + "\n".join(parts)) if parts else ""

    @staticmethod
    def _fmt_sub(sub: Subscription) -> str:
        parts: list[str] = []
        if sub.filter_types:
            parts.append("types=" + ",".join(sub.filter_types))
        if sub.filter_regex:
            parts.append("regex=" + ",".join(sub.filter_regex))
        return f" ({'; '.join(parts)})" if parts else ""

    # ───────────────────── 指令：自检 ─────────────────────

    @filter.command("bili_state")
    async def cmd_bili_state(self, event: AstrMessageEvent):
        """插件状态自检。"""
        self._refresh_config()
        errors, warnings = self._validate_config()
        lines: list[str] = ["bili_state 自检结果"]

        if errors:
            lines.append("\n[FAIL] 配置检查未通过：")
            lines.extend(f"- {e}" for e in errors)
            if warnings:
                lines.append("\n[WARN] 配置警告：")
                lines.extend(f"- {w}" for w in warnings)
            lines.append("\n请先修复配置后再执行 /bili_state。")
            yield event.plain_result("\n".join(lines))
            return

        lines.append("\n[OK] 配置检查通过")
        if warnings:
            lines.append("[WARN] 配置警告：")
            lines.extend(f"- {w}" for w in warnings)

        icon = lambda s: {"ok": "[OK]", "warn": "[WARN]"}.get(s, "[FAIL]")  # noqa: E731
        cs, cm = await self._check_cookie()
        lines.append(f"\n{icon(cs)} Cookie检查：{cm}")
        ss, sm = await self._check_subscription(self._session_key(event))
        lines.append(f"{icon(ss)} 订阅检查：{sm}")

        lines.append("\n功能开关：")
        lines.extend([
            f"- 链接解析: {'开启' if self.enable_bili_parse else '关闭'}",
            f"- 下载发送: {'开启' if self.enable_video_download else '关闭'}",
            f"- 下载画质: {self.video_quality_label}",
            f"- 时长上限: {'不限制' if self.video_max_duration_seconds == 0 else f'{self.video_max_duration_seconds}s'}",
            f"- 大小上限: {'不限制' if self.video_max_size_mb == 0 else f'{self.video_max_size_mb}MB'}",
            f"- 自动删缓存: {'是' if self.video_auto_delete else '否'}",
        ])

        lines.append("\n重试策略：")
        lines.extend([
            f"- 轮询重试: 0（零重试）",
            f"- 解析/自检重试: {self.http_retry_count}",
            f"- 下载重试: {self.video_retry_count}",
            f"- 重试间隔: {self.http_retry_delay_seconds}s × 次数",
        ])

        final = "fail" if "fail" in (cs, ss) else ("warn" if "warn" in (cs, ss) or warnings else "ok")
        tail = {
            "ok": "\n自检通过：核心功能可用。",
            "warn": "\n自检完成：存在警告，请按提示优化配置。",
        }
        lines.append(tail.get(final, "\n自检完成：存在失败项，请按提示修复。"))
        yield event.plain_result("\n".join(lines))

    # ───────────────────── 指令：订阅管理 ─────────────────────

    @filter.command("bili_sub")
    async def cmd_bili_sub(self, event: AstrMessageEvent):
        """订阅UP主动态。用法：/bili_sub <UID> [过滤器...]"""
        parsed = self._parse_uid_args((event.message_str or "").split())
        if not parsed:
            yield event.plain_result("参数错误。用法：/bili_sub <B站UID> [过滤器...]")
            return
        uid, ft, fr = parsed
        session = self._session_key(event)
        items = await self._fetch_dynamic_items(uid)
        last_id = str(items[0].get("id_str") or "") if items else ""
        _, exists = self._upsert_subscription(session, uid, ft, fr, last_id)
        self._save_subscriptions()
        detail = self._fmt_filter(ft, fr)
        if exists:
            yield event.plain_result(f"该 UID 已订阅，已更新过滤条件：{uid}{detail}")
        else:
            yield event.plain_result(f"订阅成功：UID {uid}{detail}")

    @filter.command("bili_sub_del")
    async def cmd_bili_sub_del(self, event: AstrMessageEvent):
        """取消订阅。用法：/bili_sub_del <UID>"""
        args = (event.message_str or "").split()
        uid_str = args[1] if len(args) >= 2 else ""
        if not uid_str.isdigit():
            yield event.plain_result("参数错误。用法：/bili_sub_del <B站UID>")
            return
        session = self._session_key(event)
        uid = int(uid_str)
        old = self.subscriptions.get(session, [])
        new = [s for s in old if s.uid != uid]
        if len(new) == len(old):
            yield event.plain_result("未找到该订阅。")
            return
        if new:
            self.subscriptions[session] = new
        else:
            self.subscriptions.pop(session, None)
        self._save_subscriptions()
        yield event.plain_result(f"已取消订阅 UID {uid}")

    @filter.command("bili_sub_list")
    async def cmd_bili_sub_list(self, event: AstrMessageEvent):
        """查看当前会话订阅列表。"""
        subs = self.subscriptions.get(self._session_key(event), [])
        if not subs:
            yield event.plain_result("当前会话没有订阅。")
            return
        lines = ["当前订阅列表："]
        for i, s in enumerate(subs, 1):
            lines.append(f"{i}. UID {s.uid}{self._fmt_sub(s)}")
        yield event.plain_result("\n".join(lines))

    @filter.command("bili_sub_test")
    async def cmd_bili_sub_test(self, event: AstrMessageEvent):
        """测试动态抓取与过滤。用法：/bili_sub_test <UID> [过滤器...]"""
        parsed = self._parse_uid_args((event.message_str or "").split())
        if not parsed:
            yield event.plain_result("参数错误。用法：/bili_sub_test <B站UID> [过滤器...]")
            return
        uid, ft, fr = parsed
        test_sub = Subscription(uid=uid, filter_types=ft, filter_regex=fr)
        items = await self._fetch_dynamic_items(uid)
        if not items:
            yield event.plain_result("未获取到动态数据，请稍后重试。")
            return
        for item in items:
            if is_top_dynamic(item) or should_filter_dynamic(item, test_sub):
                continue
            chain = build_dynamic_chain(item)
            detail = self._fmt_filter(ft, fr)
            if detail:
                chain.append(Comp.Plain(f"\n\n测试过滤条件: {detail.strip()}"))
            yield event.chain_result(chain)
            return
        yield event.plain_result("测试完成：最新动态都被当前过滤条件过滤掉了。")

    # ───────────────────── 指令：管理员 ─────────────────────

    @filter.command("bili_global_sub")
    async def cmd_bili_global_sub(self, event: AstrMessageEvent):
        """管理员全局订阅。用法：/bili_global_sub <会话UMO> <UID> [过滤器...]"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        args = (event.message_str or "").split()
        session = args[1].strip() if len(args) >= 3 else ""
        parsed = self._parse_uid_args(args, uid_pos=2) if len(args) >= 3 else None
        if not session or not parsed:
            yield event.plain_result("参数错误。用法：/bili_global_sub <会话UMO> <B站UID> [过滤器...]")
            return
        uid, ft, fr = parsed
        items = await self._fetch_dynamic_items(uid)
        last_id = str(items[0].get("id_str") or "") if items else ""
        _, exists = self._upsert_subscription(session, uid, ft, fr, last_id)
        self._save_subscriptions()
        yield event.plain_result(
            f"{'已更新' if exists else '已添加'}全局订阅：{session} -> UID {uid}"
        )

    @filter.command("bili_global_del")
    async def cmd_bili_global_del(self, event: AstrMessageEvent):
        """管理员删除全局订阅。用法：/bili_global_del <会话UMO> [UID]"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        args = (event.message_str or "").split()
        session = args[1].strip() if len(args) >= 2 else ""
        if not session:
            yield event.plain_result("参数错误。用法：/bili_global_del <会话UMO> [B站UID]")
            return
        uid_str = args[2].strip() if len(args) >= 3 else ""
        if not uid_str:
            if session in self.subscriptions:
                self.subscriptions.pop(session)
                self._save_subscriptions()
                yield event.plain_result(f"已删除会话 {session} 的全部订阅。")
            else:
                yield event.plain_result("未找到该会话的订阅。")
            return
        if not uid_str.isdigit():
            yield event.plain_result("UID 必须是数字。")
            return
        uid = int(uid_str)
        old = self.subscriptions.get(session, [])
        new = [s for s in old if s.uid != uid]
        if len(new) == len(old):
            yield event.plain_result("未找到指定订阅。")
            return
        if new:
            self.subscriptions[session] = new
        else:
            self.subscriptions.pop(session, None)
        self._save_subscriptions()
        yield event.plain_result(f"已删除全局订阅：{session} -> UID {uid}")

    @filter.command("bili_global_list")
    async def cmd_bili_global_list(self, event: AstrMessageEvent):
        """管理员查看全局订阅。"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        if not self.subscriptions:
            yield event.plain_result("当前没有任何订阅。")
            return
        lines: list[str] = ["全局订阅列表："]
        for session, subs in self.subscriptions.items():
            if not subs:
                continue
            lines.append(f"- {session}")
            for s in subs:
                lines.append(f"  • UID {s.uid}{self._fmt_sub(s)}")
        yield event.plain_result("\n".join(lines))

    # ───────────────────── 消息事件：链接解析 ─────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def handle_bili_parse(self, event: AstrMessageEvent):
        if not self.enable_bili_parse or event.get_sender_id() == event.get_self_id():
            return

        ref: dict[str, Any] | None = None
        matched = False

        # 1. 纯文本提取
        tokens = extract_links_and_ids(event.message_str or "")
        if tokens:
            matched = True
            ref = await self._extract_video_ref_from_tokens(tokens)

        # 2. JSON 卡片提取
        if not ref:
            links = self._extract_json_links_from_event(event)
            bili_links = [lk for lk in links if RE_MESSAGE.search(lk)]
            if bili_links:
                matched = True
                ref = await self._extract_video_ref_from_tokens(bili_links)
                if not ref:
                    logger.warning("从 QQ 小程序卡片提取到 B站链接但无法解析: %s", bili_links)

        if matched:
            event.stop_event()
        if not ref:
            return

        # 可选下载（不再展示视频详细信息）
        info = await self._get_video_info(
            ref, retry_count=self.video_retry_count,
        ) if self.enable_video_download else None
        await self._send_video_download(event, ref, info)
