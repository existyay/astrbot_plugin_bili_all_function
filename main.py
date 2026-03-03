import asyncio
import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register


BILI_BV_PATTERN = r"\bBV[0-9A-Za-z]{10}\b"
BILI_AV_PATTERN = r"\bav\d+\b"
BILI_SHORT_LINK_PATTERN = r"https?://(?:b23\.tv|bili2233\.cn)/[^\s'\"<>]+"
BILI_VIDEO_LINK_PATTERN = r"https?://(?:www\.|m\.)?bilibili\.com/video/[^\s'\"<>]+"
BILI_MESSAGE_PATTERN = (
    r"(?:"
    + BILI_BV_PATTERN
    + r"|"
    + BILI_AV_PATTERN
    + r"|"
    + BILI_SHORT_LINK_PATTERN
    + r"|"
    + BILI_VIDEO_LINK_PATTERN
    + r")"
)

VALID_FILTER_TYPES = {
    "forward",
    "lottery",
    "video",
    "article",
    "draw",
    "live",
    "forward_lottery",
}

QUALITY_TO_QN = {
    "360P": 16,
    "480P": 32,
    "720P": 64,
    "1080P": 80,
    "1080P+": 112,
    "1080P60": 116,
    "4K": 120,
}


@dataclass
class Subscription:
    uid: int
    last_dynamic_id: str = ""
    is_live: bool = False
    filter_types: List[str] = field(default_factory=list)
    filter_regex: List[str] = field(default_factory=list)
    recent_ids: List[str] = field(default_factory=list)


@register("astrbot_plugin_bili_all_function", "existyay", "B站链接解析与UP动态订阅", "0.2.0")
class BiliAllPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config or context.get_config() or {}
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False

        self.enable_bili_parse = True
        self.enable_parse_miniapp = True
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
        self.bili_cookie = ""

        self._refresh_config()

        self.data_path = Path(__file__).resolve().parent / "data" / "subscriptions.json"
        self.video_cache_dir = Path(__file__).resolve().parent / "data" / "cache" / "videos"
        self.subscriptions: Dict[str, List[Subscription]] = {}

    async def initialize(self):
        self._load_subscriptions()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="bili-dynamic-poll")
        logger.info("BiliAll 插件初始化完成，订阅数: %d", self._subscription_count())

    async def terminate(self):
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._save_subscriptions()

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
            if parsed > 0:
                return parsed
        except Exception:
            pass
        return default

    def _get_config_value(self, key: str, default: Any) -> Any:
        keys = key.split(".")
        value: Any = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    def _refresh_config(self) -> None:
        self.enable_bili_parse = bool(self._get_config_value("features.enable_bili_parse", True))
        self.enable_parse_miniapp = bool(self._get_config_value("features.enable_parse_miniapp", True))
        self.poll_interval_seconds = self._coerce_positive_int(
            self._get_config_value("subscription.poll_interval_seconds", 300),
            300,
        )
        self.timeout_seconds = self._coerce_positive_int(
            self._get_config_value("subscription.timeout_seconds", 15),
            15,
        )
        self.dynamic_limit = self._coerce_positive_int(
            self._get_config_value("subscription.dynamic_limit", 3),
            3,
        )
        self.uid_task_gap_seconds = self._coerce_positive_int(
            self._get_config_value("subscription.uid_task_gap_seconds", 1),
            1,
        )
        self.recent_ids_limit = self._coerce_positive_int(
            self._get_config_value("subscription.recent_ids_limit", 20),
            20,
        )

        self.enable_video_download = bool(
            self._get_config_value("video_download.enable", False)
        )
        self.video_quality_label = str(
            self._get_config_value("video_download.quality", "720P")
        ).upper()
        if self.video_quality_label not in QUALITY_TO_QN:
            self.video_quality_label = "720P"
        self.video_max_duration_seconds = self._coerce_positive_int(
            self._get_config_value("video_download.max_duration_seconds", 600),
            600,
        )
        self.video_max_size_mb = self._coerce_positive_int(
            self._get_config_value("video_download.max_size_mb", 100),
            100,
        )
        self.video_auto_delete = bool(
            self._get_config_value("video_download.auto_delete", True)
        )
        self.bili_cookie = str(
            self._get_config_value("video_download.bili_cookie", "")
        ).strip()

        raw_admin_ids = self._get_config_value("permission.admin_ids", "")
        parsed_admin_ids: set[str] = set()
        if isinstance(raw_admin_ids, list):
            parsed_admin_ids = {str(x).strip() for x in raw_admin_ids if str(x).strip()}
        elif isinstance(raw_admin_ids, str):
            parts = re.split(r"[,\s\n]+", raw_admin_ids.strip()) if raw_admin_ids.strip() else []
            parsed_admin_ids = {p.strip() for p in parts if p.strip()}
        self.admin_ids = parsed_admin_ids

    def _build_http_headers(self, *, with_referer: bool = False, with_cookie: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "User-Agent": "Mozilla/5.0",
        }
        if with_referer:
            headers["Referer"] = "https://www.bilibili.com/"
        if with_cookie and self.bili_cookie:
            headers["Cookie"] = self.bili_cookie
        return headers

    def _subscription_count(self) -> int:
        return sum(len(v) for v in self.subscriptions.values())

    def _load_subscriptions(self) -> None:
        try:
            if not self.data_path.exists():
                self.data_path.parent.mkdir(parents=True, exist_ok=True)
                self.subscriptions = {}
                return
            raw = json.loads(self.data_path.read_text(encoding="utf-8"))
            loaded: Dict[str, List[Subscription]] = {}
            for session, items in (raw or {}).items():
                if not isinstance(items, list):
                    continue
                normalized: List[Subscription] = []
                for item in items:
                    try:
                        filter_types = item.get("filter_types") or []
                        if not isinstance(filter_types, list):
                            filter_types = []
                        filter_types = [str(x).strip() for x in filter_types if str(x).strip() in VALID_FILTER_TYPES]

                        filter_regex = item.get("filter_regex") or []
                        if not isinstance(filter_regex, list):
                            filter_regex = []
                        filter_regex = [str(x).strip() for x in filter_regex if str(x).strip()]

                        recent_ids = item.get("recent_ids") or []
                        if not isinstance(recent_ids, list):
                            recent_ids = []
                        recent_ids = [str(x) for x in recent_ids if str(x)]

                        normalized.append(
                            Subscription(
                                uid=int(item.get("uid")),
                                last_dynamic_id=str(item.get("last_dynamic_id") or ""),
                                is_live=bool(item.get("is_live", False)),
                                filter_types=filter_types,
                                filter_regex=filter_regex,
                                recent_ids=recent_ids,
                            )
                        )
                    except Exception:
                        continue
                loaded[str(session)] = normalized
            self.subscriptions = loaded
        except Exception as exc:
            logger.error("加载订阅数据失败: %s", str(exc))
            self.subscriptions = {}

    def _save_subscriptions(self) -> None:
        try:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            dumped = {
                session: [asdict(item) for item in items]
                for session, items in self.subscriptions.items()
                if items
            }
            self.data_path.write_text(
                json.dumps(dumped, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.error("保存订阅数据失败: %s", str(exc))

    def _session_key(self, event: AstrMessageEvent) -> str:
        value = getattr(event, "unified_msg_origin", None)
        if value:
            return str(value)
        sender_id = event.get_sender_id()
        return f"sender:{sender_id}"

    @staticmethod
    def _extract_links_and_ids(text: str) -> List[str]:
        if not text:
            return []
        links: List[str] = []
        links.extend(re.findall(BILI_VIDEO_LINK_PATTERN, text, re.IGNORECASE))
        links.extend(re.findall(BILI_SHORT_LINK_PATTERN, text, re.IGNORECASE))
        links.extend(re.findall(BILI_BV_PATTERN, text))
        links.extend(re.findall(BILI_AV_PATTERN, text, re.IGNORECASE))
        seen = set()
        unique: List[str] = []
        for item in links:
            lower = item.lower()
            if lower in seen:
                continue
            seen.add(lower)
            unique.append(item)
        return unique

    @staticmethod
    def _extract_urls_from_text(text: str) -> List[str]:
        if not text:
            return []
        return re.findall(r"https?://[^\s'\"<>]+", text)

    def _coerce_json_payload(self, json_component: Any) -> Optional[dict]:
        def unwrap(value: Any, depth: int = 0) -> Optional[dict]:
            if depth > 5 or value is None:
                return None
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                try:
                    return unwrap(json.loads(stripped), depth + 1)
                except Exception:
                    return None
            if isinstance(value, dict):
                if "data" in value and len(value.keys()) == 1:
                    return unwrap(value.get("data"), depth + 1)
                return value
            if isinstance(value, list):
                for item in value:
                    got = unwrap(item, depth + 1)
                    if got:
                        return got
            if hasattr(value, "data"):
                return unwrap(getattr(value, "data"), depth + 1)
            return None

        return unwrap(json_component)

    def extract_links_from_json(self, json_component: Any) -> List[str]:
        links: List[str] = []
        try:
            json_data = self._coerce_json_payload(json_component)
            if not json_data:
                return links

            def walk(obj: Any) -> List[str]:
                found: List[str] = []
                if isinstance(obj, dict):
                    for val in obj.values():
                        if isinstance(val, str):
                            found.extend(self._extract_urls_from_text(val))
                        elif isinstance(val, (dict, list)):
                            found.extend(walk(val))
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, str):
                            found.extend(self._extract_urls_from_text(item))
                        elif isinstance(item, (dict, list)):
                            found.extend(walk(item))
                return found

            links.extend(walk(json_data))

            meta = json_data.get("meta", {}) if isinstance(json_data, dict) else {}
            detail_1 = meta.get("detail_1", {}) if isinstance(meta, dict) else {}
            if isinstance(detail_1, dict):
                for key in ("qqdocurl", "url", "desc"):
                    val = detail_1.get(key)
                    if isinstance(val, str):
                        links.extend(self._extract_urls_from_text(val))

            news = meta.get("news", {}) if isinstance(meta, dict) else {}
            if isinstance(news, dict):
                for key in ("jumpUrl", "url"):
                    val = news.get(key)
                    if isinstance(val, str):
                        links.extend(self._extract_urls_from_text(val))
        except Exception as exc:
            logger.warning("解析 JSON 卡片失败: %s", str(exc))

        dedup = list(dict.fromkeys(links))
        return dedup

    def _has_json_component(self, event: AstrMessageEvent) -> bool:
        if not hasattr(event, "message_obj") or not hasattr(event.message_obj, "message"):
            return False
        for component in event.message_obj.message:
            if isinstance(component, dict):
                comp_type = str(component.get("type") or "").lower()
                if comp_type == "reply":
                    continue
                if "json" in comp_type:
                    return True
                continue
            if isinstance(component, Comp.Json):
                return True
            comp_type = str(getattr(component, "type", "") or "").lower()
            if "json" in comp_type:
                return True
        return False

    async def _resolve_video_ref_from_links(self, links: List[str]) -> Dict[str, Any] | None:
        for link in links:
            ref = await self._extract_video_ref(link)
            if ref:
                return ref
        return None

    async def _resolve_short_url(self, short_url: str) -> str:
        def _resolve() -> str:
            req = Request(short_url, headers=self._build_http_headers(with_referer=True, with_cookie=True))
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                return str(resp.geturl())

        try:
            return await asyncio.to_thread(_resolve)
        except Exception:
            return short_url

    async def _http_get_json(self, base_url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        def _request() -> Dict[str, Any]:
            query = urlencode(params, doseq=True)
            full_url = f"{base_url}?{query}" if query else base_url
            req = Request(full_url, headers=self._build_http_headers(with_referer=True, with_cookie=True))
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body)

        try:
            return await asyncio.to_thread(_request)
        except Exception as exc:
            logger.warning("请求失败: %s", str(exc))
            return None

    async def _probe_content_length(self, url: str) -> Optional[int]:
        def _head() -> Optional[int]:
            req = Request(
                url,
                headers=self._build_http_headers(with_referer=True, with_cookie=True),
                method="HEAD",
            )
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                length = resp.headers.get("Content-Length")
                if not length:
                    return None
                return int(length)

        try:
            return await asyncio.to_thread(_head)
        except Exception:
            return None

    async def _download_file_with_limit(
        self,
        url: str,
        save_path: Path,
        max_bytes: int,
    ) -> int:
        def _download() -> int:
            req = Request(url, headers=self._build_http_headers(with_referer=True, with_cookie=True))
            total = 0
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                with save_path.open("wb") as f:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if max_bytes > 0 and total > max_bytes:
                            raise ValueError("视频超过大小限制")
                        f.write(chunk)
            return total

        return await asyncio.to_thread(_download)

    @staticmethod
    def _quality_candidates(label: str) -> List[int]:
        selected = QUALITY_TO_QN.get(label.upper(), 64)
        levels = sorted(set(QUALITY_TO_QN.values()), reverse=True)
        try:
            start = levels.index(selected)
        except ValueError:
            start = levels.index(64)
        return levels[start:]

    async def _fetch_playurl(
        self,
        *,
        bvid: str,
        cid: int,
        qn: int,
    ) -> Optional[Dict[str, Any]]:
        params = {
            "bvid": bvid,
            "cid": cid,
            "qn": qn,
            "fnval": 0,
            "fnver": 0,
            "fourk": 1,
        }
        payload = await self._http_get_json("https://api.bilibili.com/x/player/playurl", params)
        if not payload or payload.get("code") != 0:
            return None
        data = payload.get("data") or {}

        durl = data.get("durl") or []
        if isinstance(durl, list) and durl:
            first = durl[0] if isinstance(durl[0], dict) else {}
            stream_url = str(first.get("url") or "").strip()
            if stream_url:
                return {
                    "url": stream_url,
                    "quality": int(data.get("quality") or qn),
                }

        dash = data.get("dash") or {}
        videos = dash.get("video") if isinstance(dash, dict) else None
        if isinstance(videos, list) and videos:
            first = videos[0] if isinstance(videos[0], dict) else {}
            stream_url = str(first.get("baseUrl") or first.get("base_url") or "").strip()
            if stream_url:
                return {
                    "url": stream_url,
                    "quality": int(first.get("id") or qn),
                }
        return None

    async def _download_bili_video_file(self, ref: Dict[str, Any]) -> Tuple[Optional[Path], str]:
        info = await self._get_video_info(ref)
        if not info:
            return None, "获取视频信息失败"

        bvid = str(info.get("bvid") or ref.get("bvid") or "").strip()
        if not bvid:
            return None, "无法识别 bvid"

        pages = info.get("pages") or []
        page_count = len(pages) if pages else 1
        page_index = min(int(ref.get("page", 0) or 0), max(page_count - 1, 0))
        page = pages[page_index] if pages and page_index < len(pages) else {}
        cid = int(page.get("cid") or 0)
        if cid <= 0:
            return None, "无法识别 cid"

        duration = int(page.get("duration") or info.get("duration") or 0)
        if self.video_max_duration_seconds > 0 and duration > self.video_max_duration_seconds:
            return None, f"视频时长超过限制({duration}s > {self.video_max_duration_seconds}s)"

        stream_info: Optional[Dict[str, Any]] = None
        for qn in self._quality_candidates(self.video_quality_label):
            stream_info = await self._fetch_playurl(bvid=bvid, cid=cid, qn=qn)
            if stream_info:
                break
        if not stream_info:
            return None, "未获取到可下载的视频流"

        stream_url = str(stream_info.get("url") or "").strip()
        if not stream_url:
            return None, "视频流地址为空"

        max_bytes = self.video_max_size_mb * 1024 * 1024 if self.video_max_size_mb > 0 else 0
        if max_bytes > 0:
            content_len = await self._probe_content_length(stream_url)
            if content_len and content_len > max_bytes:
                return None, f"视频大小超过限制({content_len / 1024 / 1024:.1f}MB > {self.video_max_size_mb}MB)"

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = self.video_cache_dir / f"{bvid}_p{page_index + 1}_{now}.mp4"

        try:
            real_size = await self._download_file_with_limit(
                stream_url,
                save_path,
                max_bytes=max_bytes,
            )
            if real_size <= 0 or not save_path.exists():
                return None, "下载结果为空"
            return save_path, ""
        except ValueError as exc:
            try:
                save_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None, str(exc)
        except Exception as exc:
            try:
                save_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None, f"下载失败: {exc}"

    async def _send_downloaded_video(self, event: AstrMessageEvent, ref: Dict[str, Any]) -> None:
        if not self.enable_video_download:
            return

        video_path: Optional[Path] = None
        try:
            video_path, err = await self._download_bili_video_file(ref)
            if not video_path:
                if err:
                    await self.context.send_message(
                        event.unified_msg_origin,
                        MessageEventResult(chain=[Comp.Plain(f"⚠️ {err}")]),
                    )
                return

            chain = [Comp.Plain("📦 已下载并发送视频文件"), Comp.Video.fromFileSystem(str(video_path.resolve()))]
            await self.context.send_message(
                event.unified_msg_origin,
                MessageEventResult(chain=chain),
            )
        except Exception as exc:
            logger.warning("发送下载视频失败: %s", str(exc))
            await self.context.send_message(
                event.unified_msg_origin,
                MessageEventResult(chain=[Comp.Plain(f"⚠️ 视频发送失败: {exc}")]),
            )
        finally:
            if self.video_auto_delete and video_path and video_path.exists():
                try:
                    await asyncio.to_thread(video_path.unlink, True)
                except Exception:
                    pass

    @staticmethod
    def _parse_page_index(url_or_text: str) -> int:
        try:
            parsed = urlparse(url_or_text)
            query = parsed.query or ""
            if "p=" not in query:
                return 0
            segs = [part for part in query.split("&") if part.startswith("p=")]
            if not segs:
                return 0
            return max(int(segs[0].split("=", 1)[1]) - 1, 0)
        except Exception:
            return 0

    async def _extract_video_ref(self, text: str) -> Dict[str, Any] | None:
        tokens = self._extract_links_and_ids(text)
        for token in tokens:
            current = token
            if re.match(BILI_SHORT_LINK_PATTERN, token, re.IGNORECASE):
                current = await self._resolve_short_url(token)

            bvid_match = re.search(BILI_BV_PATTERN, current)
            if bvid_match:
                return {
                    "bvid": bvid_match.group(0),
                    "aid": None,
                    "source": token,
                    "page": self._parse_page_index(current),
                }

            avid_match = re.search(BILI_AV_PATTERN, current, re.IGNORECASE)
            if avid_match:
                return {
                    "bvid": None,
                    "aid": int(avid_match.group(0)[2:]),
                    "source": token,
                    "page": self._parse_page_index(current),
                }
        return None

    async def _get_video_info(self, ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        params = {}
        if ref.get("bvid"):
            params["bvid"] = ref["bvid"]
        elif ref.get("aid"):
            params["aid"] = ref["aid"]
        else:
            return None

        url = "https://api.bilibili.com/x/web-interface/view"
        payload = await self._http_get_json(url, params)
        if not payload:
            return None

        if payload.get("code") != 0:
            return None
        return payload.get("data")

    @staticmethod
    def _format_count(value: int) -> str:
        if value >= 100000000:
            return f"{value / 100000000:.1f}亿"
        if value >= 10000:
            return f"{value / 10000:.1f}万"
        return str(value)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        m, s = divmod(max(seconds, 0), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def _build_video_chain(self, info: Dict[str, Any], ref: Dict[str, Any]) -> List[object]:
        chain: List[object] = []

        stat = info.get("stat", {})
        owner = info.get("owner", {})
        pages = info.get("pages") or []
        page_count = len(pages) if pages else 1
        page_index = min(int(ref.get("page", 0) or 0), max(page_count - 1, 0))
        page = pages[page_index] if pages and page_index < len(pages) else {}

        bvid = info.get("bvid", "")
        title = info.get("title", "未知标题")
        up_name = owner.get("name", "未知UP")
        desc = (info.get("desc") or "").strip()
        duration = int(page.get("duration") or info.get("duration") or 0)
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        part = page.get("part") or ""
        cover_url = str(info.get("pic") or "").strip()

        if cover_url.startswith("http://") or cover_url.startswith("https://"):
            try:
                chain.append(Comp.Image.fromURL(cover_url))
            except Exception:
                pass

        lines = [
            f"🎬 {title}",
            f"👤 UP: {up_name}",
            f"▶️ 播放: {self._format_count(int(stat.get('view', 0) or 0))}",
            f"👍 点赞: {self._format_count(int(stat.get('like', 0) or 0))}",
            f"⏱️ 时长: {self._format_duration(duration)}",
        ]
        if page_count > 1:
            lines.append(f"📄 分P: {page_index + 1}/{page_count} {part}".strip())
        if desc:
            lines.append(f"📝 简介: {desc[:120]}{'...' if len(desc) > 120 else ''}")
        if url:
            lines.append(url)
        chain.append(Comp.Plain("\n".join(lines)))
        return chain

    async def _render_video_text(self, ref: Dict[str, Any]) -> Optional[str]:
        info = await self._get_video_info(ref)
        if not info:
            return None
        chain = self._build_video_chain(info, ref)
        if not chain:
            return None
        for item in chain:
            if isinstance(item, Comp.Plain):
                return getattr(item, "text", None)
        return None

    async def _build_video_chain_from_ref(self, ref: Dict[str, Any]) -> List[object]:
        info = await self._get_video_info(ref)
        if not info:
            return []
        return self._build_video_chain(info, ref)

    async def _fetch_latest_dynamic(self, uid: int) -> Optional[Dict[str, Any]]:
        items = await self._fetch_dynamic_items(uid)
        if not items:
            return None
        return items[0]

    async def _fetch_dynamic_items(self, uid: int) -> List[Dict[str, Any]]:
        url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
        params = {"host_mid": uid}
        payload = await self._http_get_json(url, params)
        if not payload:
            return []

        if payload.get("code") != 0:
            return []
        items = payload.get("data", {}).get("items") or []
        return [item for item in items if isinstance(item, dict) and item.get("id_str")]

    async def _fetch_live_status(self, uid: int) -> Optional[Dict[str, Any]]:
        url = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"
        params = {"uids[]": uid}
        payload = await self._http_get_json(url, params)
        if not payload:
            return None

        if payload.get("code") != 0:
            return None
        data = payload.get("data") or {}
        return data.get(str(uid))

    @staticmethod
    def _dynamic_summary(item: Dict[str, Any]) -> str:
        modules = item.get("modules", {}) if isinstance(item, dict) else {}
        author = modules.get("module_author", {}) if isinstance(modules, dict) else {}
        dynamic = modules.get("module_dynamic", {}) if isinstance(modules, dict) else {}
        desc = dynamic.get("desc", {}) if isinstance(dynamic, dict) else {}
        text = desc.get("text") or ""
        name = author.get("name") or "未知UP"
        dyn_id = item.get("id_str") or ""
        jump = f"https://t.bilibili.com/{dyn_id}" if dyn_id else ""
        lines = [f"📣 {name} 发布了新动态"]
        if text:
            lines.append(text[:180] + ("..." if len(text) > 180 else ""))
        if jump:
            lines.append(jump)
        return "\n".join(lines)

    def _extract_dynamic_cover(self, item: Dict[str, Any]) -> str:
        modules = item.get("modules") or {}
        if not isinstance(modules, dict):
            return ""
        dynamic = modules.get("module_dynamic") or {}
        if not isinstance(dynamic, dict):
            return ""
        major = dynamic.get("major") or {}
        if not isinstance(major, dict):
            return ""

        archive = major.get("archive") or {}
        if isinstance(archive, dict):
            cover = archive.get("cover")
            if isinstance(cover, str) and cover.startswith(("http://", "https://")):
                return cover

        opus = major.get("opus") or {}
        if isinstance(opus, dict):
            pics = opus.get("pics") or []
            if isinstance(pics, list) and pics:
                first = pics[0]
                if isinstance(first, dict):
                    for key in ("url", "img_src", "src"):
                        val = first.get(key)
                        if isinstance(val, str) and val.startswith(("http://", "https://")):
                            return val
                elif isinstance(first, str) and first.startswith(("http://", "https://")):
                    return first
        return ""

    def _build_dynamic_chain(self, item: Dict[str, Any]) -> List[object]:
        chain: List[object] = []
        cover = self._extract_dynamic_cover(item)
        if cover:
            try:
                chain.append(Comp.Image.fromURL(cover))
            except Exception:
                pass
        chain.append(Comp.Plain(self._dynamic_summary(item)))
        return chain

    @staticmethod
    def _is_top_dynamic(item: Dict[str, Any]) -> bool:
        modules = item.get("modules") or {}
        module_tag = modules.get("module_tag") if isinstance(modules, dict) else None
        if not isinstance(module_tag, dict):
            return False
        return str(module_tag.get("text") or "") == "置顶"

    @staticmethod
    def _extract_dynamic_text(item: Dict[str, Any]) -> str:
        modules = item.get("modules") or {}
        if not isinstance(modules, dict):
            return ""
        dynamic = modules.get("module_dynamic") or {}
        if not isinstance(dynamic, dict):
            return ""

        parts: List[str] = []
        desc = dynamic.get("desc") or {}
        if isinstance(desc, dict):
            text = desc.get("text")
            if text:
                parts.append(str(text))

        major = dynamic.get("major") or {}
        if isinstance(major, dict):
            opus = major.get("opus") or {}
            if isinstance(opus, dict):
                summary = opus.get("summary") or {}
                if isinstance(summary, dict) and summary.get("text"):
                    parts.append(str(summary.get("text")))

            archive = major.get("archive") or {}
            if isinstance(archive, dict):
                if archive.get("title"):
                    parts.append(str(archive.get("title")))
                if archive.get("desc"):
                    parts.append(str(archive.get("desc")))

        return "\n".join([x for x in parts if x]).strip()

    @staticmethod
    def _is_lottery_text(text: str) -> bool:
        if not text:
            return False
        if "互动抽奖" in text:
            return True
        return bool(re.search(r"恭喜.*等\d+位同学中奖", text))

    def _is_forward_lottery(self, item: Dict[str, Any]) -> bool:
        try:
            orig = item.get("orig") or {}
            modules = orig.get("modules") or {}
            dynamic = modules.get("module_dynamic") or {}
            major = dynamic.get("major") or {}
            opus = major.get("opus") or {}
            summary = opus.get("summary") or {}
            nodes = summary.get("rich_text_nodes") or []
            if nodes and isinstance(nodes[0], dict):
                return str(nodes[0].get("text") or "") == "互动抽奖"
        except Exception:
            return False
        return False

    def _match_filter_regex(self, text: str, filter_regex: List[str]) -> bool:
        if not text or not filter_regex:
            return False
        for pattern in filter_regex:
            try:
                if re.search(pattern, text):
                    return True
            except re.error:
                logger.warning("无效正则表达式: %s", pattern)
                continue
        return False

    def _should_filter_dynamic(self, item: Dict[str, Any], sub: Subscription) -> bool:
        filter_types = set(sub.filter_types or [])
        dynamic_type = str(item.get("type") or "")
        text = self._extract_dynamic_text(item)

        if dynamic_type == "DYNAMIC_TYPE_FORWARD":
            if "forward" in filter_types:
                return True
            if "forward_lottery" in filter_types and self._is_forward_lottery(item):
                return True
            if "lottery" in filter_types and self._is_lottery_text(text):
                return True
        elif dynamic_type in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
            if "draw" in filter_types:
                return True
            if "lottery" in filter_types and self._is_lottery_text(text):
                return True
        elif dynamic_type == "DYNAMIC_TYPE_AV":
            if "video" in filter_types:
                return True
        elif dynamic_type == "DYNAMIC_TYPE_ARTICLE":
            if "article" in filter_types:
                return True

        if self._match_filter_regex(text, sub.filter_regex or []):
            return True
        return False

    @staticmethod
    def _parse_filter_args(args: List[str]) -> Tuple[List[str], List[str]]:
        filter_types: List[str] = []
        filter_regex: List[str] = []
        for arg in args:
            token = str(arg).strip()
            if not token:
                continue
            if token in VALID_FILTER_TYPES:
                filter_types.append(token)
            else:
                filter_regex.append(token)
        filter_types = list(dict.fromkeys(filter_types))
        filter_regex = list(dict.fromkeys(filter_regex))
        return filter_types, filter_regex

    def _append_recent_id(self, sub: Subscription, dyn_id: str) -> None:
        if not dyn_id:
            return
        recent = sub.recent_ids or []
        if dyn_id in recent:
            recent.remove(dyn_id)
        recent.append(dyn_id)
        if len(recent) > self.recent_ids_limit:
            recent = recent[-self.recent_ids_limit :]
        sub.recent_ids = recent

    def _build_uid_targets(self) -> Dict[int, List[Tuple[str, Subscription]]]:
        uid_targets: Dict[int, List[Tuple[str, Subscription]]] = {}
        for session, items in self.subscriptions.items():
            for sub in items:
                uid_targets.setdefault(sub.uid, []).append((session, sub))
        return uid_targets

    async def _process_single_subscription(
        self,
        session: str,
        sub: Subscription,
        dynamic_items: List[Dict[str, Any]],
        live_info: Optional[Dict[str, Any]],
    ) -> bool:
        changed = False
        known_ids = {sub.last_dynamic_id, *(sub.recent_ids or [])}
        known_ids.discard("")

        new_items: List[Dict[str, Any]] = []
        for item in dynamic_items:
            if self._is_top_dynamic(item):
                continue
            dyn_id = str(item.get("id_str") or "")
            if not dyn_id:
                continue
            if dyn_id in known_ids:
                break
            new_items.append(item)

        sent = 0
        for item in reversed(new_items):
            dyn_id = str(item.get("id_str") or "")
            if not dyn_id:
                continue

            if not self._should_filter_dynamic(item, sub) and sent < self.dynamic_limit:
                sent += 1
                chain = self._build_dynamic_chain(item)
                await self.context.send_message(
                    session,
                    MessageEventResult(chain=chain),
                )

            sub.last_dynamic_id = dyn_id
            self._append_recent_id(sub, dyn_id)
            changed = True

        if live_info and "live" not in set(sub.filter_types or []):
            live_status = int(live_info.get("live_status") or 0)
            now_live = live_status == 1
            if now_live != sub.is_live:
                sub.is_live = now_live
                changed = True
                uname = live_info.get("uname") or f"UID {sub.uid}"
                if now_live:
                    title = live_info.get("title") or ""
                    room_id = live_info.get("room_id") or ""
                    link = f"https://live.bilibili.com/{room_id}" if room_id else ""
                    msg = f"🔴 {uname} 开播了\n{title}".strip()
                    if link:
                        msg = msg + f"\n{link}"
                else:
                    msg = f"⚪ {uname} 下播了"
                await self.context.send_message(
                    session, MessageEventResult().message(msg)
                )

        return changed

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        sender_id = str(event.get_sender_id())
        return sender_id in self.admin_ids

    def _validate_state_config(self) -> Tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []

        if self.video_quality_label not in QUALITY_TO_QN:
            errors.append(f"video_download.quality 非法: {self.video_quality_label}")

        if self.poll_interval_seconds <= 0:
            errors.append("subscription.poll_interval_seconds 必须 > 0")
        if self.timeout_seconds <= 0:
            errors.append("subscription.timeout_seconds 必须 > 0")
        if self.dynamic_limit <= 0:
            errors.append("subscription.dynamic_limit 必须 > 0")
        if self.video_max_duration_seconds <= 0:
            errors.append("video_download.max_duration_seconds 必须 > 0")
        if self.video_max_size_mb <= 0:
            errors.append("video_download.max_size_mb 必须 > 0")

        qn = QUALITY_TO_QN.get(self.video_quality_label, 64)
        if self.enable_video_download and qn >= 80 and not self.bili_cookie:
            warnings.append("已启用高画质下载但未填写 Cookie，可能无法获取 1080P 及以上")
        if self.bili_cookie and "SESSDATA=" not in self.bili_cookie:
            warnings.append("Cookie 中未检测到 SESSDATA，登录态可能无效")
        return errors, warnings

    async def _check_cookie_state(self) -> Tuple[str, str]:
        payload = await self._http_get_json("https://api.bilibili.com/x/web-interface/nav", {})
        if not payload:
            return "fail", "请求 nav 接口失败（网络或超时）"
        if payload.get("code") != 0:
            return "fail", f"nav 接口返回错误 code={payload.get('code')}"

        data = payload.get("data") or {}
        is_login = bool(data.get("isLogin"))
        uname = str(data.get("uname") or "")

        if self.bili_cookie:
            if is_login:
                return "ok", f"Cookie 有效，当前登录账号: {uname or '未知'}"
            return "fail", "已填写 Cookie 但未登录，Cookie 可能失效"

        if is_login:
            return "warn", f"未配置 Cookie，但环境已有登录态: {uname or '未知'}"
        return "warn", "未配置 Cookie，下载画质可能受限"

    async def _check_subscription_state(self, current_session: str) -> Tuple[str, str]:
        total_sessions = len(self.subscriptions)
        total_subs = self._subscription_count()
        loop_ok = bool(self._running and self._poll_task and not self._poll_task.done())

        test_uid: Optional[int] = None
        session_subs = self.subscriptions.get(current_session, [])
        if session_subs:
            test_uid = session_subs[0].uid
        elif self.subscriptions:
            first_items = next(iter(self.subscriptions.values()), [])
            if first_items:
                test_uid = first_items[0].uid

        if not test_uid:
            loop_msg = "轮询任务运行中" if loop_ok else "轮询任务未运行"
            return "warn", f"当前无订阅数据（sessions={total_sessions}, subs={total_subs}，{loop_msg}）"

        items = await self._fetch_dynamic_items(test_uid)
        live = await self._fetch_live_status(test_uid)
        if not items and live is None:
            return "fail", f"订阅拉取失败：UID {test_uid} 的动态与直播接口均无响应"

        loop_msg = "轮询任务运行中" if loop_ok else "轮询任务未运行"
        return (
            "ok" if loop_ok else "warn",
            f"订阅可用：UID {test_uid} 动态{len(items)}条，直播状态{'可用' if live is not None else '不可用'}（{loop_msg}）",
        )

    @filter.command("bili_state")
    async def cmd_bili_state(self, event: AstrMessageEvent):
        """插件状态自检。用法：/bili_state"""
        self._refresh_config()

        errors, warnings = self._validate_state_config()
        lines: List[str] = ["🔎 bili_state 自检结果"]

        if errors:
            lines.append("\n❌ 配置检查未通过：")
            for item in errors:
                lines.append(f"- {item}")
            if warnings:
                lines.append("\n⚠️ 配置警告：")
                for item in warnings:
                    lines.append(f"- {item}")
            lines.append("\n请先修复配置后再执行 /bili_state。")
            yield event.plain_result("\n".join(lines))
            return

        lines.append("\n✅ 配置检查通过")
        if warnings:
            lines.append("⚠️ 配置警告：")
            for item in warnings:
                lines.append(f"- {item}")

        cookie_status, cookie_msg = await self._check_cookie_state()
        cookie_prefix = "✅" if cookie_status == "ok" else ("⚠️" if cookie_status == "warn" else "❌")
        lines.append(f"\n{cookie_prefix} Cookie检查：{cookie_msg}")

        session = self._session_key(event)
        sub_status, sub_msg = await self._check_subscription_state(session)
        sub_prefix = "✅" if sub_status == "ok" else ("⚠️" if sub_status == "warn" else "❌")
        lines.append(f"{sub_prefix} 订阅检查：{sub_msg}")

        lines.append("\n🧩 功能开关：")
        lines.append(f"- 链接解析: {'开启' if self.enable_bili_parse else '关闭'}")
        lines.append(f"- 小程序解析: {'开启' if self.enable_parse_miniapp else '关闭'}")
        lines.append(f"- 下载发送: {'开启' if self.enable_video_download else '关闭'}")
        lines.append(f"- 下载画质: {self.video_quality_label}")
        lines.append(f"- 时长上限: {self.video_max_duration_seconds}s")
        lines.append(f"- 大小上限: {self.video_max_size_mb}MB")
        lines.append(f"- 自动删缓存: {'是' if self.video_auto_delete else '否'}")

        final = "ok"
        if cookie_status == "fail" or sub_status == "fail":
            final = "fail"
        elif cookie_status == "warn" or sub_status == "warn" or bool(warnings):
            final = "warn"

        if final == "ok":
            lines.append("\n🎉 自检通过：核心功能可用。")
        elif final == "warn":
            lines.append("\n⚠️ 自检完成：存在警告，请按提示优化配置。")
        else:
            lines.append("\n❌ 自检完成：存在失败项，请按提示修复。")

        yield event.plain_result("\n".join(lines))

    def _upsert_subscription(
        self,
        session: str,
        uid: int,
        filter_types: List[str],
        filter_regex: List[str],
        latest_dyn_id: str = "",
    ) -> Tuple[Subscription, bool]:
        subs = self.subscriptions.setdefault(session, [])
        for sub in subs:
            if sub.uid == uid:
                sub.filter_types = filter_types
                sub.filter_regex = filter_regex
                return sub, True

        new_sub = Subscription(
            uid=uid,
            last_dynamic_id=latest_dyn_id,
            is_live=False,
            filter_types=filter_types,
            filter_regex=filter_regex,
            recent_ids=[],
        )
        subs.append(new_sub)
        return new_sub, False

    async def _poll_loop(self):
        while self._running:
            try:
                changed = False
                uid_targets = self._build_uid_targets()
                for uid, targets in uid_targets.items():
                    dynamic_items = await self._fetch_dynamic_items(uid)
                    live = await self._fetch_live_status(uid)

                    for session, sub in targets:
                        if await self._process_single_subscription(
                            session=session,
                            sub=sub,
                            dynamic_items=dynamic_items,
                            live_info=live,
                        ):
                            changed = True

                    await asyncio.sleep(self.uid_task_gap_seconds)

                if changed:
                    self._save_subscriptions()
                await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("订阅轮询异常: %s", str(exc))
                await asyncio.sleep(5)

    @filter.command("bili_sub")
    async def cmd_bili_sub(self, event: AstrMessageEvent):
        """订阅指定UID的UP主动态。用法：/bili_sub 123456 [过滤器...]"""
        args = (event.message_str or "").split()
        uid = args[1] if len(args) >= 2 else ""
        if not uid.isdigit():
            yield event.plain_result("参数错误。用法：/bili_sub <B站UID> [过滤器...]")
            return

        session = self._session_key(event)
        uid_int = int(uid)
        filter_types, filter_regex = self._parse_filter_args(args[2:])

        latest = await self._fetch_latest_dynamic(uid_int)
        last_id = str(latest.get("id_str") or "") if latest else ""
        _, exists = self._upsert_subscription(
            session=session,
            uid=uid_int,
            filter_types=filter_types,
            filter_regex=filter_regex,
            latest_dyn_id=last_id,
        )
        self._save_subscriptions()

        details: List[str] = []
        if filter_types:
            details.append("类型过滤: " + ", ".join(filter_types))
        if filter_regex:
            details.append("正则过滤: " + ", ".join(filter_regex))
        suffix = ("\n" + "\n".join(details)) if details else ""
        if exists:
            yield event.plain_result(f"该 UID 已订阅，已更新过滤条件：{uid_int}{suffix}")
        else:
            yield event.plain_result(f"订阅成功：UID {uid_int}{suffix}")

    @filter.command("bili_sub_del")
    async def cmd_bili_sub_del(self, event: AstrMessageEvent):
        """取消订阅。用法：/bili_sub_del 123456"""
        args = (event.message_str or "").split()
        uid = args[1] if len(args) >= 2 else ""
        if not uid.isdigit():
            yield event.plain_result("参数错误。用法：/bili_sub_del <B站UID>")
            return

        session = self._session_key(event)
        uid_int = int(uid)
        old = self.subscriptions.get(session, [])
        new = [item for item in old if item.uid != uid_int]
        if len(new) == len(old):
            yield event.plain_result("未找到该订阅。")
            return

        if new:
            self.subscriptions[session] = new
        else:
            self.subscriptions.pop(session, None)
        self._save_subscriptions()
        yield event.plain_result(f"已取消订阅 UID {uid_int}")

    @filter.command("bili_sub_list")
    async def cmd_bili_sub_list(self, event: AstrMessageEvent):
        """查看当前会话订阅列表。"""
        session = self._session_key(event)
        subs = self.subscriptions.get(session, [])
        if not subs:
            yield event.plain_result("当前会话没有订阅。")
            return
        lines = ["当前订阅列表："]
        for idx, item in enumerate(subs, start=1):
            extra: List[str] = []
            if item.filter_types:
                extra.append("types=" + ",".join(item.filter_types))
            if item.filter_regex:
                extra.append("regex=" + ",".join(item.filter_regex))
            suffix = f" ({'; '.join(extra)})" if extra else ""
            lines.append(f"{idx}. UID {item.uid}{suffix}")
        yield event.plain_result("\n".join(lines))

    @filter.command("bili_sub_test")
    async def cmd_bili_sub_test(self, event: AstrMessageEvent):
        """测试订阅动态抓取与过滤。用法：/bili_sub_test <B站UID> [过滤器...]"""
        args = (event.message_str or "").split()
        uid = args[1] if len(args) >= 2 else ""
        if not uid.isdigit():
            yield event.plain_result("参数错误。用法：/bili_sub_test <B站UID> [过滤器...]")
            return

        uid_int = int(uid)
        filter_types, filter_regex = self._parse_filter_args(args[2:])
        test_sub = Subscription(
            uid=uid_int,
            filter_types=filter_types,
            filter_regex=filter_regex,
        )

        items = await self._fetch_dynamic_items(uid_int)
        if not items:
            yield event.plain_result("未获取到动态数据，请稍后重试。")
            return

        valid_items = [item for item in items if not self._is_top_dynamic(item)]
        if not valid_items:
            yield event.plain_result("没有可用于测试的动态（可能全是置顶）。")
            return

        for item in valid_items:
            if self._should_filter_dynamic(item, test_sub):
                continue
            chain = self._build_dynamic_chain(item)
            if filter_types or filter_regex:
                suffix_parts: List[str] = []
                if filter_types:
                    suffix_parts.append("types=" + ",".join(filter_types))
                if filter_regex:
                    suffix_parts.append("regex=" + ",".join(filter_regex))
                chain.append(Comp.Plain("\n\n测试过滤条件: " + " ; ".join(suffix_parts)))
            yield event.chain_result(chain)
            return

        yield event.plain_result("测试完成：最新动态都被当前过滤条件过滤掉了。")

    @filter.command("bili_global_sub")
    async def cmd_bili_global_sub(self, event: AstrMessageEvent):
        """管理员全局订阅。用法：/bili_global_sub <会话UMO> <B站UID> [过滤器...]"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return

        args = (event.message_str or "").split()
        session = args[1].strip() if len(args) >= 3 else ""
        uid = args[2].strip() if len(args) >= 3 else ""
        if not session or not uid.isdigit():
            yield event.plain_result("参数错误。用法：/bili_global_sub <会话UMO> <B站UID> [过滤器...]")
            return

        uid_int = int(uid)
        filter_types, filter_regex = self._parse_filter_args(args[3:])
        latest = await self._fetch_latest_dynamic(uid_int)
        last_id = str(latest.get("id_str") or "") if latest else ""
        _, exists = self._upsert_subscription(
            session=session,
            uid=uid_int,
            filter_types=filter_types,
            filter_regex=filter_regex,
            latest_dyn_id=last_id,
        )
        self._save_subscriptions()

        if exists:
            yield event.plain_result(f"已更新全局订阅：{session} -> UID {uid_int}")
        else:
            yield event.plain_result(f"已添加全局订阅：{session} -> UID {uid_int}")

    @filter.command("bili_global_del")
    async def cmd_bili_global_del(self, event: AstrMessageEvent):
        """管理员删除全局订阅。用法：/bili_global_del <会话UMO> [B站UID]"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return

        args = (event.message_str or "").split()
        session = args[1].strip() if len(args) >= 2 else ""
        uid = args[2].strip() if len(args) >= 3 else ""
        if not session:
            yield event.plain_result("参数错误。用法：/bili_global_del <会话UMO> [B站UID]")
            return

        if not uid:
            if session in self.subscriptions:
                self.subscriptions.pop(session, None)
                self._save_subscriptions()
                yield event.plain_result(f"已删除会话 {session} 的全部订阅。")
            else:
                yield event.plain_result("未找到该会话的订阅。")
            return

        if not uid.isdigit():
            yield event.plain_result("UID 必须是数字。")
            return

        uid_int = int(uid)
        old = self.subscriptions.get(session, [])
        new = [item for item in old if item.uid != uid_int]
        if len(new) == len(old):
            yield event.plain_result("未找到指定订阅。")
            return
        if new:
            self.subscriptions[session] = new
        else:
            self.subscriptions.pop(session, None)
        self._save_subscriptions()
        yield event.plain_result(f"已删除全局订阅：{session} -> UID {uid_int}")

    @filter.command("bili_global_list")
    async def cmd_bili_global_list(self, event: AstrMessageEvent):
        """管理员查看全局订阅。"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        if not self.subscriptions:
            yield event.plain_result("当前没有任何订阅。")
            return

        lines: List[str] = ["全局订阅列表："]
        for session, items in self.subscriptions.items():
            if not items:
                continue
            lines.append(f"- {session}")
            for item in items:
                extra: List[str] = []
                if item.filter_types:
                    extra.append("types=" + ",".join(item.filter_types))
                if item.filter_regex:
                    extra.append("regex=" + ",".join(item.filter_regex))
                suffix = f" ({'; '.join(extra)})" if extra else ""
                lines.append(f"  • UID {item.uid}{suffix}")
        yield event.plain_result("\n".join(lines))

    @filter.regex(BILI_MESSAGE_PATTERN, priority=10)
    async def handle_bili_parse(self, event: AstrMessageEvent):
        if not self.enable_bili_parse:
            return
        if event.get_sender_id() == event.get_self_id():
            return
        if self._has_json_component(event):
            return
        ref = await self._extract_video_ref(event.message_str or "")
        if not ref:
            return
        chain = await self._build_video_chain_from_ref(ref)
        if chain:
            yield event.chain_result(chain)
        await self._send_downloaded_video(event, ref)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def handle_json_card_parse(self, event: AstrMessageEvent):
        """解析 QQ 转发的 Bilibili 小程序 JSON 卡片。"""
        if not self.enable_parse_miniapp:
            return
        if event.get_sender_id() == event.get_self_id():
            return
        if not self._has_json_component(event):
            return

        links: List[str] = []
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "message"):
            for component in event.message_obj.message:
                comp_payload: Any = component
                is_json = False

                if isinstance(component, dict):
                    comp_type = str(component.get("type") or "").lower()
                    if comp_type == "reply":
                        continue
                    is_json = "json" in comp_type
                    comp_payload = component.get("data") or component
                else:
                    if isinstance(component, Comp.Json):
                        is_json = True
                        comp_payload = getattr(component, "data", component)
                    else:
                        comp_type = str(getattr(component, "type", "") or "").lower()
                        is_json = "json" in comp_type
                        if is_json and hasattr(component, "data"):
                            comp_payload = getattr(component, "data")

                if is_json:
                    links.extend(self.extract_links_from_json(comp_payload))

        if not links:
            return

        unique_links = list(dict.fromkeys(links))
        bili_links = [link for link in unique_links if re.search(BILI_MESSAGE_PATTERN, link, re.IGNORECASE)]
        if not bili_links:
            return

        ref = await self._resolve_video_ref_from_links(bili_links)
        if not ref:
            logger.warning("从 QQ 小程序卡片提取到 B站链接但无法解析: %s", bili_links)
            return

        chain = await self._build_video_chain_from_ref(ref)
        if chain:
            yield event.chain_result(chain)
        await self._send_downloaded_video(event, ref)
