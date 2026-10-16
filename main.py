"""AstrBot 插件：B站链接解析 + UP主动态订阅 + 直播推送。

设计要点（适配 AstrBot 4.x 最新插件接口）：

* 不再使用已废弃的 ``@register`` 装饰器，插件元数据统一由 ``metadata.yaml`` 提供；
* 使用 ``Star`` 自带的 ``self.logger`` 插件级日志；
* 数据目录通过 ``StarTools.get_data_dir(PLUGIN_NAME)`` 显式定位；
* 主动推送使用 ``MessageChain`` / ``context.send_message``；
* 管理员指令同时兼容 AstrBot 原生管理员身份（``event.is_admin()``）与配置白名单；
* 额外提供 ``bili_video_info`` 函数调用工具（function-calling）。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import (
    AstrMessageEvent,
    MessageChain,
    MessageEventResult,
    filter,
)
from astrbot.api.star import Context, Star, StarTools

try:  # pragma: no cover - 包内导入 / 单文件加载两种场景
    from .bili_constants import (
        API_DYNAMIC_FEED,
        API_LIVE_ROOM,
        API_LIVE_STATUS,
        API_NAV,
        API_PLAYURL,
        API_VIDEO_VIEW,
        DEFAULT_DYNAMIC_LIMIT,
        DEFAULT_EMPTY_RETRY_COUNT,
        DEFAULT_EMPTY_RETRY_DELAY_SECONDS,
        DEFAULT_HTTP_RETRY_COUNT,
        DEFAULT_HTTP_RETRY_DELAY_SECONDS,
        DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DEFAULT_QN,
        DEFAULT_QUALITY_LABEL,
        DEFAULT_RECENT_IDS_LIMIT,
        DEFAULT_TIMEOUT_SECONDS,
        DEFAULT_UID_CONCURRENCY,
        DEFAULT_UID_TASK_GAP_SECONDS,
        DEFAULT_VIDEO_MAX_DURATION_SECONDS,
        DEFAULT_VIDEO_MAX_SIZE_MB,
        DEFAULT_VIDEO_RETRY_COUNT,
        DOWNLOAD_CHUNK_SIZE,
        DYNAMIC_FEED_FEATURES,
        DYNAMIC_SILENT_CODES,
        DYNAMIC_SPACE_URL,
        DYNAMIC_TEXT_WRAP_WIDTH,
        DYNAMIC_TEXT_WRAP_WIDTH_MAX,
        MAX_JSON_BYTES,
        MAX_LLM_URL_LEN,
        MAX_MESSAGE_SCAN_LEN,
        MAX_SESSIONS,
        MAX_SUBS_PER_SESSION,
        PLUGIN_DISPLAY_NAME,
        PLUGIN_NAME,
        PLUGIN_VERSION,
        QUALITY_TO_QN,
        RE_AV,
        RE_BV,
        RE_MESSAGE,
        RE_SHORT_LINK,
        RE_URL,
        RETRYABLE_HTTP,
        SUBSCRIPTIONS_SCHEMA_KEY,
        SUBSCRIPTIONS_SCHEMA_VERSION,
        USER_AGENT,
        VALID_FILTER_TYPES,
        WEB_API_STATUS_ROUTE,
    )
    from .bili_helpers import (
        compile_filter_patterns,
        extract_links_and_ids,
        format_bytes,
        format_group_map_line,
        host_of,
        is_blocked_session,
        is_trusted_host,
        is_valid_uid,
        mask_secret,
        normalize_modules,
        normalize_session,
        parse_blocked_groups,
        parse_filter_args,
        parse_group_map,
        parse_page_index,
        render_group_map,
        resolve_video_page,
        sanitize_cookie,
        sanitize_filename,
        split_group_map,
        validate_filter_patterns,
        validate_group_map,
    )
    from .bili_logic import (
        append_recent_id,
        build_dynamic_chain,
        build_live_chain,
        build_uid_targets,
        build_video_chain,
        build_video_text,
        collect_new_dynamics,
        latest_dynamic_id,
        pick_push_batch,
        should_filter_dynamic,
    )
    from .bili_models import GroupBinding, Subscription
except ImportError:  # pragma: no cover
    from bili_constants import (
        API_DYNAMIC_FEED,
        API_LIVE_ROOM,
        API_LIVE_STATUS,
        API_NAV,
        API_PLAYURL,
        API_VIDEO_VIEW,
        DEFAULT_DYNAMIC_LIMIT,
        DEFAULT_EMPTY_RETRY_COUNT,
        DEFAULT_EMPTY_RETRY_DELAY_SECONDS,
        DEFAULT_HTTP_RETRY_COUNT,
        DEFAULT_HTTP_RETRY_DELAY_SECONDS,
        DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DEFAULT_QN,
        DEFAULT_QUALITY_LABEL,
        DEFAULT_RECENT_IDS_LIMIT,
        DEFAULT_TIMEOUT_SECONDS,
        DEFAULT_UID_CONCURRENCY,
        DEFAULT_UID_TASK_GAP_SECONDS,
        DEFAULT_VIDEO_MAX_DURATION_SECONDS,
        DEFAULT_VIDEO_MAX_SIZE_MB,
        DEFAULT_VIDEO_RETRY_COUNT,
        DOWNLOAD_CHUNK_SIZE,
        DYNAMIC_FEED_FEATURES,
        DYNAMIC_SILENT_CODES,
        DYNAMIC_SPACE_URL,
        DYNAMIC_TEXT_WRAP_WIDTH,
        DYNAMIC_TEXT_WRAP_WIDTH_MAX,
        MAX_JSON_BYTES,
        MAX_LLM_URL_LEN,
        MAX_MESSAGE_SCAN_LEN,
        MAX_SESSIONS,
        MAX_SUBS_PER_SESSION,
        PLUGIN_DISPLAY_NAME,
        PLUGIN_NAME,
        PLUGIN_VERSION,
        QUALITY_TO_QN,
        RE_AV,
        RE_BV,
        RE_MESSAGE,
        RE_SHORT_LINK,
        RE_URL,
        RETRYABLE_HTTP,
        SUBSCRIPTIONS_SCHEMA_KEY,
        SUBSCRIPTIONS_SCHEMA_VERSION,
        USER_AGENT,
        VALID_FILTER_TYPES,
        WEB_API_STATUS_ROUTE,
    )
    from bili_helpers import (
        compile_filter_patterns,
        extract_links_and_ids,
        format_bytes,
        format_group_map_line,
        host_of,
        is_blocked_session,
        is_trusted_host,
        is_valid_uid,
        mask_secret,
        normalize_modules,
        normalize_session,
        parse_blocked_groups,
        parse_filter_args,
        parse_group_map,
        parse_page_index,
        render_group_map,
        resolve_video_page,
        sanitize_cookie,
        sanitize_filename,
        split_group_map,
        validate_filter_patterns,
        validate_group_map,
    )
    from bili_logic import (
        append_recent_id,
        build_dynamic_chain,
        build_live_chain,
        build_uid_targets,
        build_video_chain,
        build_video_text,
        collect_new_dynamics,
        latest_dynamic_id,
        pick_push_batch,
        should_filter_dynamic,
    )
    from bili_models import GroupBinding, Subscription

#: 可重试的 HTTP 状态码（模块级别名，保持向后兼容的导入路径）。
_RETRYABLE_HTTP = RETRYABLE_HTTP

_STATUS_ICONS = {"ok": "[正常]", "warn": "[警告]", "fail": "[异常]"}


def _status_icon(status: str) -> str:
    return _STATUS_ICONS.get(status, _STATUS_ICONS["fail"])


def _fsync_dir(path: Path) -> None:
    """尽力而为地 fsync 目录，确保 ``os.replace`` 的改名在断电后仍可见。

    Windows 不允许以只读方式打开目录，故失败时静默忽略（不影响主流程）。
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ParsedUidArgs:
    """命令参数解析结果（``error`` 非空表示过滤器非法）。"""

    uid: int
    filter_types: list[str]
    filter_regex: list[str]
    error: str = ""


#: 指令 handler 的通用签名（供群屏蔽闸门装饰器复用）。
CommandHandler = Callable[[Any, AstrMessageEvent], AsyncGenerator[Any, None]]


def _blocked_guard(handler: CommandHandler) -> CommandHandler:
    """群屏蔽闸门：被屏蔽会话里的指令直接静默忽略（不回复，避免暴露机器人存在）。

    仅用于指令 handler；链接解析与推送链路的闸门分别在 ``handle_bili_parse``
    与 ``_safe_send`` 中显式判断。
    """

    @wraps(handler)
    async def wrapper(self: Any, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if self._is_blocked(self._session_key(event)):
            return
        async for item in handler(self, event):
            yield item

    return wrapper


class BiliAllPlugin(Star):
    """B站链接解析与UP主动态订阅插件。

    指令：
      /bili_sub <UID> [过滤器...]        订阅 UP 主动态
      /bili_sub_del <UID>                取消订阅
      /bili_sub_list                     查看当前会话订阅
      /bili_sub_test <UID> [过滤器...]    测试动态抓取与过滤
      /bili_state                        插件状态自检
      /bili_global_sub <UMO> <UID> [...]  管理员：为指定会话订阅
      /bili_global_del <UMO> [UID]        管理员：删除指定会话订阅
      /bili_global_list                   管理员：查看全部订阅
      /bili_map_sync                      管理员：立即应用「群订阅映射表」并回写

    群聊级开关（设置面板）：
      * ``permission.blocked_groups``：屏蔽的群，完全不启用本插件；
      * ``subscription.group_subscriptions``：在设置里直接写「群 = UID」映射，
        无需在群里执行订阅指令；群内指令建立的订阅会自动同步回该配置项。
    """

    # ───────────────────── 生命周期 ─────────────────────

    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.context = context
        # AstrBot >= 4.20 的 Star 会注入插件级 logger；旧版本回退到全局 logger。
        self.logger = getattr(self, "logger", None) or logger
        # 保留原始配置对象：AstrBotConfig 支持 save_config()，用于把「群内命令
        # 建立的订阅」回写进设置面板（纯 dict 场景只更新内存态，不回写文件）。
        self.config_obj: Any = config if isinstance(config, dict) else None
        self.config: dict[str, Any] = dict(config) if config else {}
        self._poll_task: asyncio.Task | None = None
        self._running = False
        self._started_at = time.time()
        self._web_api_registered = False

        # 运行统计（供 /bili_state 与插件页面展示，不落盘）
        self.stats: dict[str, int] = dict.fromkeys(
            (
                "http_requests",
                "http_failures",
                "http_silent",
                "poll_cycles",
                "poll_errors",
                "dynamic_pushes",
                "live_pushes",
                "downloads",
                "download_bytes",
                "send_failures",
            ),
            0,
        )

        # 请求节流（降低触发 B站风控的概率）
        self._throttle_lock = asyncio.Lock()
        self._last_request_at = 0.0

        # 配置默认值（会被 _refresh_config 覆盖）
        self.enable_bili_parse = True
        self.send_video_info = True
        self.enable_llm_tool = True
        self.poll_interval_seconds = DEFAULT_POLL_INTERVAL_SECONDS
        self.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self.dynamic_limit = DEFAULT_DYNAMIC_LIMIT
        self.uid_task_gap_seconds = DEFAULT_UID_TASK_GAP_SECONDS
        self.uid_concurrency = DEFAULT_UID_CONCURRENCY
        self.recent_ids_limit = DEFAULT_RECENT_IDS_LIMIT
        self.dynamic_wrap_width = DYNAMIC_TEXT_WRAP_WIDTH
        # 群屏蔽列表与「群订阅映射表」（设置面板）
        self.blocked_groups: tuple[str, ...] = ()
        self.group_map_text = ""
        self.group_bindings: tuple[GroupBinding, ...] = ()
        self.group_map_errors: list[str] = []
        self.sync_group_map = True
        self.group_map_authoritative = False
        self._last_mirror_text: str | None = None
        self.empty_retry_count = DEFAULT_EMPTY_RETRY_COUNT
        self.empty_retry_delay_seconds = DEFAULT_EMPTY_RETRY_DELAY_SECONDS
        self.http_retry_count = DEFAULT_HTTP_RETRY_COUNT
        self.http_retry_delay_seconds = DEFAULT_HTTP_RETRY_DELAY_SECONDS
        self.min_request_interval_seconds = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS
        self.admin_ids: set[str] = set()
        self.enable_video_download = False
        self.video_quality_label = DEFAULT_QUALITY_LABEL
        self.video_max_duration_seconds = DEFAULT_VIDEO_MAX_DURATION_SECONDS
        self.video_max_size_mb = DEFAULT_VIDEO_MAX_SIZE_MB
        self.video_auto_delete = True
        self.video_retry_count = DEFAULT_VIDEO_RETRY_COUNT
        self.bili_cookie = ""
        self._http_session: aiohttp.ClientSession | None = None

        self._refresh_config()

        plugin_data_dir = self._resolve_data_dir()
        self.data_path = plugin_data_dir / "subscriptions.json"
        self.video_cache_dir = plugin_data_dir / "cache" / "videos"
        self.subscriptions: dict[str, list[Subscription]] = {}
        self.data_schema_version = SUBSCRIPTIONS_SCHEMA_VERSION

    def _resolve_data_dir(self) -> Path:
        """定位插件数据目录（显式传入插件名，避免依赖调用栈推断）。"""
        try:
            return Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - 仅在宿主环境异常时触发
            fallback = Path.cwd() / "data" / "plugin_data" / PLUGIN_NAME
            logger.warning("获取插件数据目录失败，回退到 %s：%s", fallback, exc)
            return fallback

    async def initialize(self) -> None:
        """插件启用时调用：加载订阅、建立会话、注册插件页面 API 并启动轮询。"""
        self._started_at = time.time()
        self._load_subscriptions()
        self._refresh_config()
        # 先把已有订阅（旧方法：群内指令建立的）同步进设置面板，便于查看/接管
        self._sync_group_map_config()
        await self._ensure_http_session()
        self._register_web_api()
        self._running = True
        self._start_poll_task()
        if self.group_map_errors:
            for err in self.group_map_errors[:5]:
                self.logger.warning("群订阅映射表配置有问题：%s", err)
        self.logger.info(
            "BiliAll 插件初始化完成：订阅 %d 个（会话 %d 个），配置映射 %d 条（会话 %d 个），"
            "屏蔽群聊 %d 个，轮询间隔 %ds，UID 并发 %d，下载发送 %s，Cookie %s",
            self._sub_count(),
            len(self.subscriptions),
            len(self.group_bindings),
            len({b.session for b in self.group_bindings}),
            len(self.blocked_groups),
            self.poll_interval_seconds,
            self.uid_concurrency,
            "开启" if self.enable_video_download else "关闭",
            "已配置" if self.bili_cookie else "未配置",
        )

    def _register_web_api(self) -> None:
        """注册只读状态接口，供插件页面（Views）调用。

        旧版 AstrBot 没有 ``register_web_api``，这里做能力探测后静默跳过，
        不影响插件在低版本上的可用性。
        """
        if self._web_api_registered:
            return
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            self.logger.debug("当前 AstrBot 版本不支持 register_web_api，跳过插件页面接口注册")
            return
        try:
            register(
                WEB_API_STATUS_ROUTE,
                self._web_status_handler,
                ["GET"],
                "B站插件运行状态（只读）",
            )
        except Exception as exc:  # noqa: BLE001 - 注册失败不应影响插件加载
            self.logger.warning("注册插件页面接口失败（不影响核心功能）：%s", exc)
            return
        self._web_api_registered = True
        self.logger.debug("已注册插件页面接口：%s", WEB_API_STATUS_ROUTE)

    async def _web_status_handler(self, **_: Any) -> Any:
        """插件页面调用入口：返回脱敏后的运行状态 JSON。"""
        payload = {"status": "ok", "data": self.health_snapshot()}
        try:
            from astrbot.api.web import json_response
        except ImportError:  # pragma: no cover - 4.18 等旧版本没有该模块，退回同构 dict
            return payload
        return json_response(payload)

    def health_snapshot(self) -> dict[str, Any]:
        """运行状态快照（只读、已脱敏，供插件页面 / 自检 / 监控使用）。"""
        loop_ok = bool(self._running and self._poll_task and not self._poll_task.done())
        return {
            "plugin": PLUGIN_NAME,
            "display_name": PLUGIN_DISPLAY_NAME,
            "version": PLUGIN_VERSION,
            "uptime_seconds": int(max(0.0, time.time() - self._started_at)),
            "healthy": loop_ok,
            "poll_running": loop_ok,
            "sessions": len(self.subscriptions),
            "subscriptions": self._sub_count(),
            "schema_version": self.data_schema_version,
            "cookie_configured": bool(self.bili_cookie),
            "cookie_masked": mask_secret(self.bili_cookie),
            "features": {
                "parse": self.enable_bili_parse,
                "video_info": self.send_video_info,
                "download": self.enable_video_download,
                "llm_tool": self.enable_llm_tool,
            },
            "poll": {
                "interval_seconds": self.poll_interval_seconds,
                "concurrency": self.uid_concurrency,
                "dynamic_limit": self.dynamic_limit,
                "min_request_interval_seconds": self.min_request_interval_seconds,
                "wrap_width": self.dynamic_wrap_width,
            },
            "blocked_groups": len(self.blocked_groups),
            "group_map": {
                "entries": len(self.group_bindings),
                "sessions": len({b.session for b in self.group_bindings}),
                "synced": self._last_mirror_text is not None,
                "authoritative": self.group_map_authoritative,
                "errors": len(self.group_map_errors),
            },
            "stats": dict(self.stats),
        }

    def _start_poll_task(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = asyncio.create_task(
            self._poll_loop(),
            name="bili-dynamic-poll",
        )

    async def terminate(self) -> None:
        """插件卸载/重载时调用：停止轮询、关闭会话并落盘订阅数据。"""
        self._running = False
        task = self._poll_task
        self._poll_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 卸载阶段只记录，不向上抛
                self.logger.warning("轮询任务退出异常: %s", exc)
        session = self._http_session
        self._http_session = None
        if session and not session.closed:
            try:
                await session.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响卸载
                self.logger.warning("关闭 HTTP 会话失败: %s", exc)
        self._save_subscriptions()
        self.logger.info(
            "BiliAll 插件已卸载：运行 %.0fs，请求 %d 次（失败 %d），推送动态 %d 条 / 直播 %d 次",
            max(0.0, time.time() - self._started_at),
            self.stats["http_requests"],
            self.stats["http_failures"],
            self.stats["dynamic_pushes"],
            self.stats["live_pushes"],
        )

    # ───────────────────── 配置 ─────────────────────

    @staticmethod
    def _int(value: Any, default: int, *, min_val: int = 1) -> int:
        """安全整数转换，低于 *min_val* 时回退到 *default*。"""
        try:
            v = int(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return v if v >= min_val else default

    @staticmethod
    def _float(value: Any, default: float, *, min_val: float = 0.0) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return v if v >= min_val else default

    @staticmethod
    def _str_list(value: Any) -> list[str]:
        """把 ``str`` / ``list`` 形式的配置项规范化为字符串列表。"""
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return [p for p in re.split(r"[,\s]+", value.strip()) if p]
        return []

    def _cfg(self, key: str, default: Any = None) -> Any:
        """按点分路径读取嵌套配置。

        优先从原始配置对象（AstrBotConfig）读取，这样设置面板保存后无需重启
        插件即可生效；纯 dict / 无配置时回退到构造时的快照。
        """
        node: Any = self.config_obj if self.config_obj is not None else self.config
        for k in key.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(k)
            if node is None:
                return default
        return node

    def _set_cfg(self, key: str, value: Any) -> bool:
        """写入嵌套配置并持久化（返回是否写进了配置对象）。"""
        node: Any = self.config_obj if self.config_obj is not None else self.config
        parts = key.split(".")
        for k in parts[:-1]:
            if not isinstance(node, dict):
                return False
            child = node.get(k)
            if not isinstance(child, dict):
                return False
            node = child
        if not isinstance(node, dict):
            return False
        node[parts[-1]] = value
        return True

    def _persist_config(self) -> bool:
        """把内存中的配置落盘（AstrBotConfig.save_config）；失败时记录并返回 False。"""
        save = getattr(self.config_obj, "save_config", None)
        if not callable(save):
            return False
        try:
            save()
        except Exception as exc:  # noqa: BLE001 - 面板配置写失败不应影响推送
            self.logger.warning("保存插件配置失败（群订阅映射未回写）：%s", exc)
            return False
        return True

    def _refresh_config(self) -> None:
        c, ci, cf = self._cfg, self._int, self._float
        self.enable_bili_parse = bool(c("features.enable_bili_parse", True))
        self.send_video_info = bool(c("features.send_video_info", True))
        self.enable_llm_tool = bool(c("features.enable_llm_tool", True))

        self.poll_interval_seconds = ci(
            c("subscription.poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS),
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
        self.timeout_seconds = ci(
            c("subscription.timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            DEFAULT_TIMEOUT_SECONDS,
        )
        self.dynamic_limit = ci(
            c("subscription.dynamic_limit", DEFAULT_DYNAMIC_LIMIT),
            DEFAULT_DYNAMIC_LIMIT,
        )
        self.uid_task_gap_seconds = ci(
            c("subscription.uid_task_gap_seconds", DEFAULT_UID_TASK_GAP_SECONDS),
            DEFAULT_UID_TASK_GAP_SECONDS,
            min_val=0,
        )
        self.uid_concurrency = ci(
            c("subscription.uid_concurrency", DEFAULT_UID_CONCURRENCY),
            DEFAULT_UID_CONCURRENCY,
        )
        self.recent_ids_limit = ci(
            c("subscription.recent_ids_limit", DEFAULT_RECENT_IDS_LIMIT),
            DEFAULT_RECENT_IDS_LIMIT,
        )
        # 0 表示关闭自动换行（合法值），故 min_val=0
        self.dynamic_wrap_width = ci(
            c("subscription.wrap_width", DYNAMIC_TEXT_WRAP_WIDTH),
            DYNAMIC_TEXT_WRAP_WIDTH,
            min_val=0,
        )
        self.dynamic_wrap_width = min(self.dynamic_wrap_width, DYNAMIC_TEXT_WRAP_WIDTH_MAX)
        self.empty_retry_count = ci(
            c("subscription.empty_retry_count", DEFAULT_EMPTY_RETRY_COUNT),
            DEFAULT_EMPTY_RETRY_COUNT,
            min_val=0,
        )
        self.empty_retry_delay_seconds = cf(
            c("subscription.empty_retry_delay_seconds", DEFAULT_EMPTY_RETRY_DELAY_SECONDS),
            DEFAULT_EMPTY_RETRY_DELAY_SECONDS,
        )
        self.http_retry_count = ci(
            c("subscription.http_retry_count", DEFAULT_HTTP_RETRY_COUNT),
            DEFAULT_HTTP_RETRY_COUNT,
            min_val=0,
        )
        self.http_retry_delay_seconds = cf(
            c("subscription.http_retry_delay_seconds", DEFAULT_HTTP_RETRY_DELAY_SECONDS),
            DEFAULT_HTTP_RETRY_DELAY_SECONDS,
        )
        self.min_request_interval_seconds = cf(
            c("subscription.min_request_interval_seconds", DEFAULT_MIN_REQUEST_INTERVAL_SECONDS),
            DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        )

        self.enable_video_download = bool(c("video_download.enable", False))
        label = str(c("video_download.quality", DEFAULT_QUALITY_LABEL)).upper()
        self.video_quality_label = label if label in QUALITY_TO_QN else DEFAULT_QUALITY_LABEL
        self.video_max_duration_seconds = ci(
            c("video_download.max_duration_seconds", DEFAULT_VIDEO_MAX_DURATION_SECONDS),
            DEFAULT_VIDEO_MAX_DURATION_SECONDS,
            min_val=0,
        )
        self.video_max_size_mb = ci(
            c("video_download.max_size_mb", DEFAULT_VIDEO_MAX_SIZE_MB),
            DEFAULT_VIDEO_MAX_SIZE_MB,
            min_val=0,
        )
        self.video_auto_delete = bool(c("video_download.auto_delete", True))
        self.video_retry_count = ci(
            c("video_download.retry_count", DEFAULT_VIDEO_RETRY_COUNT),
            DEFAULT_VIDEO_RETRY_COUNT,
            min_val=0,
        )
        self.bili_cookie = sanitize_cookie(c("video_download.bili_cookie", ""))
        self.admin_ids = set(self._str_list(c("permission.admin_ids", "")))

        # 群屏蔽列表（被屏蔽的群完全不启用本插件）
        self.blocked_groups = tuple(parse_blocked_groups(c("permission.blocked_groups", "")))
        # 「群订阅映射表」：可在设置里直接写「群 = UID [过滤器]」，无需在群里发指令
        self.group_map_text = str(c("subscription.group_subscriptions", "") or "")
        self.group_bindings = tuple(parse_group_map(self.group_map_text))
        self.group_map_errors = validate_group_map(self.group_map_text)
        self.sync_group_map = bool(c("subscription.sync_group_map", True))
        self.group_map_authoritative = bool(c("subscription.group_map_authoritative", False))

    # ───────────────────── HTTP 层 ─────────────────────

    def _headers(
        self,
        *,
        referer: bool = False,
        cookie: bool = False,
        referer_uid: int | None = None,
        url: str | None = None,
    ) -> dict[str, str]:
        """构造请求头。

        ``cookie=True`` 时只有目标域名属于B站（:data:`BILI_COOKIE_HOST_SUFFIXES`）
        才会带上登录 Cookie，避免重定向到第三方域名时泄漏登录态。
        """
        h: dict[str, str] = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Origin": "https://www.bilibili.com",
        }
        if referer_uid:
            h["Referer"] = DYNAMIC_SPACE_URL.format(uid=referer_uid)
        elif referer:
            h["Referer"] = "https://www.bilibili.com/"
        if cookie and self.bili_cookie and (url is None or is_trusted_host(url)):
            h["Cookie"] = self.bili_cookie
        return h

    @staticmethod
    def _classify_exc(exc: Exception) -> tuple[str, bool]:
        """分类异常 → (标签, 是否可重试)。"""
        if isinstance(exc, aiohttp.ClientResponseError):
            code = int(getattr(exc, "status", 0) or 0)
            tag = "http_client" if 400 <= code < 500 else ("http_server" if code >= 500 else "http")
            return f"{tag}_{code}", code in RETRYABLE_HTTP
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

    async def _throttle(self) -> None:
        """限制外部请求频率，降低触发 B站风控的概率。

        间隔为 0 时直接返回（测试与高频自检场景）。
        """
        interval = max(0.0, self.min_request_interval_seconds)
        if interval <= 0:
            return
        async with self._throttle_lock:
            wait = self._last_request_at + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    def _retry_delay(self, attempt: int) -> float:
        """重试等待时间：基础间隔 × 轮次，并加入轻微抖动避免同步冲击。"""
        base = max(0.0, self.http_retry_delay_seconds) * max(1, attempt)
        if base <= 0:
            return 0.0
        return base + random.uniform(0, base * 0.25)

    @staticmethod
    async def _read_limited(resp: aiohttp.ClientResponse, limit: int) -> bytes:
        """读取响应体，超过 *limit* 字节时抛 :class:`ValueError`。"""
        if limit <= 0:
            return await resp.read()
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.content.iter_chunked(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise ValueError(f"响应体超过 {limit} 字节上限")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _request(
        self,
        *,
        url: str,
        method: str = "GET",
        headers: dict[str, str],
        read_body: bool = True,
        scene: str,
        silent_codes: set[int] | frozenset[int] | None = None,
        retry: bool = True,
        retry_count: int | None = None,
        max_bytes: int = MAX_JSON_BYTES,
    ) -> dict[str, Any] | None:
        session = await self._ensure_http_session()
        safe_headers = self._strip_untrusted_cookie(url, headers)

        effective = retry_count if retry_count is not None else self.http_retry_count
        max_att = (effective + 1) if retry else 1
        for att in range(1, max_att + 1):
            await self._throttle()
            self.stats["http_requests"] += 1
            try:
                async with session.request(
                    method,
                    url,
                    headers=safe_headers,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                ) as resp:
                    status = int(resp.status or 0)
                    if silent_codes and status in silent_codes:
                        self.stats["http_silent"] += 1
                        self.logger.debug("请求静默降级[%s][http_%s]", scene, status)
                        return None
                    if status >= 400:
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=status,
                            message=resp.reason or "",
                            headers=resp.headers,
                        )
                    body = await self._read_limited(resp, max_bytes) if read_body else b""
                    return {
                        "body": body,
                        "headers": resp.headers,
                        "status": status,
                        "final_url": str(resp.url),
                    }
            except ValueError as exc:  # 响应体超限：不重试，直接放弃
                self.stats["http_failures"] += 1
                self.logger.warning("响应体异常[%s]: %s", scene, exc)
                break
            except (aiohttp.ClientError, TimeoutError) as exc:
                if isinstance(exc, aiohttp.ClientResponseError):
                    code = int(getattr(exc, "status", 0) or 0)
                    if silent_codes and code in silent_codes:
                        self.stats["http_silent"] += 1
                        self.logger.debug("请求静默降级[%s][http_%s]", scene, code)
                        return None
                cat, retryable = self._classify_exc(exc)
                self.stats["http_failures"] += 1
                self.logger.warning("请求失败[%s][%s] %d/%d: %s", scene, cat, att, max_att, exc)
                if not retryable or att >= max_att:
                    break
                await asyncio.sleep(self._retry_delay(att))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 兜底：未知异常不重试、仅记录
                self.stats["http_failures"] += 1
                self.logger.warning("请求异常[%s] %d/%d: %s", scene, att, max_att, exc)
                break
        return None

    def _strip_untrusted_cookie(
        self,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, str]:
        """目标域名不受信任时剥离 Cookie，避免登录态随重定向泄漏。"""
        if "Cookie" not in headers or is_trusted_host(url):
            return headers
        cleaned = {k: v for k, v in headers.items() if k.lower() != "cookie"}
        self.logger.debug("目标域名 %s 不受信任，已剥离请求 Cookie", host_of(url))
        return cleaned

    async def _get_json(
        self,
        base_url: str,
        params: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        scene: str = "get_json",
        silent_codes: set[int] | frozenset[int] | None = None,
        retry: bool = True,
        retry_count: int | None = None,
    ) -> dict[str, Any] | None:
        qs = urlencode(params, doseq=True)
        full = f"{base_url}?{qs}" if qs else base_url
        result = await self._request(
            url=full,
            headers=headers or self._headers(referer=True, cookie=True, url=full),
            scene=scene,
            silent_codes=silent_codes,
            retry=retry,
            retry_count=retry_count,
        )
        if not result:
            return None
        try:
            payload = json.loads(bytes(result["body"]).decode("utf-8", errors="ignore"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.logger.warning("JSON 解析失败[%s]: %s", scene, exc)
            return None
        return payload if isinstance(payload, dict) else None

    async def _get_json_candidates(
        self,
        base_url: str,
        params: dict[str, Any],
        header_list: Sequence[dict[str, str]],
        *,
        scene: str,
        silent_codes: set[int] | frozenset[int] | None = None,
        retry: bool = True,
        retry_count: int | None = None,
    ) -> dict[str, Any] | None:
        """尝试多组 headers 直到其中一组返回有效 payload。"""
        for i, h in enumerate(header_list, 1):
            payload = await self._get_json(
                base_url,
                params,
                headers=h,
                scene=f"{scene}_{i}",
                silent_codes=silent_codes,
                retry=retry,
                retry_count=retry_count,
            )
            if payload is not None:
                return payload
        return None

    # ───────────────────── 订阅持久化 ─────────────────────

    def _sub_count(self) -> int:
        return sum(len(v) for v in self.subscriptions.values())

    def _load_subscriptions(self) -> None:
        """加载订阅数据。

        生产约束：文件损坏/类型异常时回退为空并记录错误（不影响插件启动）；
        会话数与单会话订阅数都会按上限截断，避免手工编辑的文件拖垮轮询。
        """
        if not self.data_path.exists():
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            self.subscriptions = {}
            return
        try:
            raw = json.loads(self.data_path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.logger.error("加载订阅数据失败（将使用空配置）: %s", exc)
            self.subscriptions = {}
            return
        if not isinstance(raw, dict):
            self.logger.error("订阅数据格式异常（期望 JSON 对象），已忽略")
            self.subscriptions = {}
            return

        self.data_schema_version = self._int(
            raw.pop(SUBSCRIPTIONS_SCHEMA_KEY, 1),
            1,
            min_val=1,
        )
        loaded: dict[str, list[Subscription]] = {}
        dropped = 0
        for session, items in raw.items():
            if len(loaded) >= MAX_SESSIONS:
                dropped += 1
                continue
            if not isinstance(items, list):
                continue
            subs: list[Subscription] = []
            for it in items:
                if len(subs) >= MAX_SUBS_PER_SESSION:
                    dropped += 1
                    break
                if not isinstance(it, dict):
                    continue
                try:
                    ft_raw = it.get("filter_types") or []
                    fr_raw = it.get("filter_regex") or []
                    ri_raw = it.get("recent_ids") or []
                    subs.append(
                        Subscription(
                            uid=int(it["uid"]),
                            last_dynamic_id=str(it.get("last_dynamic_id") or ""),
                            is_live=bool(it.get("is_live")),
                            filter_types=[
                                s
                                for x in (ft_raw if isinstance(ft_raw, list) else [])
                                if (s := str(x).strip()) and s in VALID_FILTER_TYPES
                            ],
                            filter_regex=[
                                s for x in (fr_raw if isinstance(fr_raw, list) else []) if (s := str(x).strip())
                            ],
                            recent_ids=[str(x) for x in (ri_raw if isinstance(ri_raw, list) else []) if x],
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    dropped += 1
                    continue
            if subs:
                loaded[str(session)] = subs
        self.subscriptions = loaded
        if dropped:
            self.logger.warning(
                "订阅数据存在 %d 处非法/超限条目已忽略（会话上限 %d，单会话订阅上限 %d）",
                dropped,
                MAX_SESSIONS,
                MAX_SUBS_PER_SESSION,
            )

    def _save_subscriptions(self) -> None:
        """原子写入订阅数据，避免进程中断导致文件损坏。

        写入流程：临时文件 → ``os.replace`` 原子替换 → 目录 ``fsync``（尽力而为）。
        返回前会把内存态标记为「已落盘」，避免重复写入。
        """
        try:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {SUBSCRIPTIONS_SCHEMA_KEY: SUBSCRIPTIONS_SCHEMA_VERSION}
            data.update({s: [asdict(sub) for sub in lst] for s, lst in self.subscriptions.items() if lst})
            tmp_path = self.data_path.parent / f"{self.data_path.name}.tmp"
            payload = json.dumps(data, ensure_ascii=False, indent=2)
            with tmp_path.open("w", encoding="utf-8") as fp:
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_path, self.data_path)
            _fsync_dir(self.data_path.parent)
            self.data_schema_version = SUBSCRIPTIONS_SCHEMA_VERSION
        except (OSError, TypeError, ValueError) as exc:
            self.logger.error("保存订阅数据失败: %s", exc)

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
            return self._coerce_json(data.data, _depth + 1)
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
        for comp in msg_obj.message or []:
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
            url=short_url,
            headers=self._headers(referer=True, cookie=True),
            read_body=False,
            scene="resolve_short_url",
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

    async def _extract_video_ref_from_tokens(self, tokens: Sequence[str]) -> dict[str, Any] | None:
        for token in tokens:
            ref = await self._extract_video_ref(token)
            if ref:
                return ref
        return None

    # ───────────────────── 视频信息 & 下载 ─────────────────────

    async def _get_video_info(
        self,
        ref: dict[str, Any],
        *,
        retry_count: int | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        if ref.get("bvid"):
            params["bvid"] = ref["bvid"]
        elif ref.get("aid"):
            params["aid"] = ref["aid"]
        else:
            return None
        payload = await self._get_json(
            API_VIDEO_VIEW,
            params,
            scene="video_info",
            retry_count=retry_count,
        )
        return payload.get("data") if payload and payload.get("code") == 0 else None

    @staticmethod
    def _quality_candidates(label: str) -> list[int]:
        selected = QUALITY_TO_QN.get(label.upper(), DEFAULT_QN)
        levels = sorted(set(QUALITY_TO_QN.values()), reverse=True)
        idx = levels.index(selected) if selected in levels else levels.index(DEFAULT_QN)
        return levels[idx:]

    async def _fetch_playurl(self, *, bvid: str, cid: int, qn: int) -> dict[str, Any] | None:
        payload = await self._get_json(
            API_PLAYURL,
            {"bvid": bvid, "cid": cid, "qn": qn, "fnval": 0, "fnver": 0, "fourk": 1},
            scene="playurl",
            retry_count=self.video_retry_count,
        )
        if not payload or payload.get("code") != 0:
            return None
        data = payload.get("data") or {}
        # durl 模式
        durl = data.get("durl") or []
        if isinstance(durl, list) and durl and isinstance(durl[0], dict):
            url = str(durl[0].get("url") or "").strip()
            if url:
                return {"url": url, "quality": self._int(data.get("quality"), qn, min_val=0)}
        # dash 模式
        dash = data.get("dash")
        videos = dash.get("video") if isinstance(dash, dict) else None
        if isinstance(videos, list) and videos and isinstance(videos[0], dict):
            url = str(videos[0].get("baseUrl") or videos[0].get("base_url") or "").strip()
            if url:
                return {"url": url, "quality": self._int(videos[0].get("id"), qn, min_val=0)}
        return None

    async def _probe_content_length(self, url: str) -> int | None:
        result = await self._request(
            url=url,
            method="HEAD",
            headers=self._headers(referer=True, cookie=True, url=url),
            read_body=False,
            scene="probe_size",
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
                    headers=self._headers(referer=True, cookie=True, url=url),
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
                        async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if max_bytes > 0 and total > max_bytes:
                                raise ValueError("视频超过大小限制")
                            f.write(chunk)
                return total
            except ValueError:
                raise
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                cat, retryable = self._classify_exc(exc)
                self.logger.warning("下载失败[%s] %d/%d: %s", cat, att, max_att, exc)
                with suppress(OSError):  # pragma: no cover - 删除失败不影响主流程
                    save_path.unlink(missing_ok=True)
                if not retryable or att >= max_att:
                    raise
                await asyncio.sleep(self.http_retry_delay_seconds * att)
        raise RuntimeError("下载失败")  # pragma: no cover - 循环内必然 return 或 raise

    async def _download_video(
        self,
        ref: dict[str, Any],
        info: dict[str, Any] | None = None,
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
        cid = self._int(page.get("cid"), 0, min_val=0)
        if cid <= 0:
            return None, "无法识别 cid"

        duration = self._int(page.get("duration") or info.get("duration"), 0, min_val=0)
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
        safe_bvid = sanitize_filename(bvid, fallback="video")
        save_path = self.video_cache_dir / f"{safe_bvid}_p{page_idx + 1}_{ts}.mp4"
        try:
            size = await self._download_file(stream_url, save_path, max_bytes)
            if size <= 0 or not save_path.exists():
                return None, "下载结果为空"
            self.stats["downloads"] += 1
            self.stats["download_bytes"] += size
            self.logger.info("视频下载完成：%s（%s）", save_path.name, format_bytes(size))
            return save_path, ""
        except ValueError as exc:
            save_path.unlink(missing_ok=True)
            return None, str(exc)
        except (aiohttp.ClientError, TimeoutError, OSError, RuntimeError) as exc:
            save_path.unlink(missing_ok=True)
            return None, f"下载失败: {exc}"

    async def _safe_send(self, session: str, chain: list[object], scene: str) -> bool:
        """主动发送消息链（失败不影响主流程）；被屏蔽会话一律不发送。"""
        if self._is_blocked(session):
            self.logger.debug("跳过向已屏蔽会话发送消息[%s]: %s", scene, session)
            return False
        try:
            await self.context.send_message(session, MessageChain(chain=chain))
            return True
        except Exception as exc:  # noqa: BLE001 - 平台侧异常种类繁多
            self.stats["send_failures"] += 1
            self.logger.warning("发送消息失败[%s]: %s", scene, exc)
            return False

    async def _send_video_download(
        self,
        event: AstrMessageEvent,
        ref: dict[str, Any],
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
                    await self._safe_send(
                        event.unified_msg_origin,
                        [Comp.Plain(f"[警告] {err}")],
                        "video_download",
                    )
                return
            await self._safe_send(
                event.unified_msg_origin,
                [Comp.Video.fromFileSystem(str(video_path.resolve()))],
                "video_download",
            )
        except (aiohttp.ClientError, TimeoutError, OSError, RuntimeError) as exc:
            self.logger.warning("发送下载视频失败: %s", exc)
            await self._safe_send(
                event.unified_msg_origin,
                [Comp.Plain(f"[警告] 视频发送失败: {exc}")],
                "video_download",
            )
        finally:
            if self.video_auto_delete and video_path and video_path.exists():
                with suppress(OSError, RuntimeError):  # pragma: no cover - 删除失败不影响主流程
                    await asyncio.to_thread(video_path.unlink, True)

    # ───────────────────── 动态 & 直播 ─────────────────────

    @staticmethod
    def _dynamic_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        """从动态接口响应中提取并规范化动态列表。"""
        if not payload or payload.get("code") != 0:
            return []
        data = payload.get("data") or {}
        raw_items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(raw_items, list):
            return []
        items = [it for it in raw_items if isinstance(it, dict) and it.get("id_str")]
        for it in items:
            normalize_modules(it)
        return items

    async def _fetch_dynamic_items(self, uid: int) -> list[dict[str, Any]]:
        """拉取 UP 主最新动态。

        B站动态接口会**偶发返回 code=0 但 items 为空**（缓存/风控边缘，实测同一 UID
        连续请求的成功率约 60%~70%），因此这里对空结果做有限重试，否则会把
        「未读到」误判为「没有新动态」，表现为**订阅永远收不到最新动态**。

        重试时每次都带一个时间戳参数（``_``），避免连续命中同一份空缓存。
        """
        attempts = max(1, self.empty_retry_count + 1)
        headers = [
            self._headers(referer=True, referer_uid=uid),
            self._headers(referer=True, referer_uid=uid, cookie=True),
        ]
        for attempt in range(1, attempts + 1):
            payload = await self._get_json_candidates(
                API_DYNAMIC_FEED,
                {
                    "host_mid": uid,
                    "timezone_offset": -480,
                    "offset": "",
                    "features": DYNAMIC_FEED_FEATURES,
                    "_": int(time.time() * 1000),
                },
                headers,
                scene="fetch_dynamic",
                silent_codes=DYNAMIC_SILENT_CODES,
                retry=False,
            )
            items = self._dynamic_items(payload)
            if items:
                return items
            if attempt < attempts:
                await asyncio.sleep(self.empty_retry_delay_seconds * attempt)
        self.logger.warning(
            "UID %s 的动态接口连续 %d 次返回空结果（B站偶发空响应/风控，或该 UP 主确实没有动态）；"
            "本轮不更新订阅锚点，下轮会重试",
            uid,
            attempts,
        )
        return []

    async def _fetch_live_status(self, uid: int) -> dict[str, Any] | None:
        payload = await self._get_json(
            API_LIVE_STATUS,
            {"uids[]": uid},
            scene="live_status",
            retry=False,
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
            API_LIVE_ROOM,
            {"mid": uid},
            scene="live_room",
            retry=False,
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
            "cover": d.get("cover") or d.get("user_cover") or "",
            "keyframe": d.get("keyframe") or "",
            "live_status": self._int(d.get("liveStatus"), 0, min_val=0),
        }

    @staticmethod
    def _is_living(live_info: dict[str, Any] | None) -> bool:
        if not isinstance(live_info, dict):
            return False
        return BiliAllPlugin._int(live_info.get("live_status"), 0, min_val=0) == 1

    # ───────────────────── 订阅处理 ─────────────────────

    def _upsert_subscription(
        self,
        session: str,
        uid: int,
        filter_types: list[str],
        filter_regex: list[str],
        latest_dyn_id: str = "",
    ) -> tuple[Subscription, bool]:
        subs = self.subscriptions.setdefault(session, [])
        for sub in subs:
            if sub.uid == uid:
                sub.filter_types = filter_types
                sub.filter_regex = filter_regex
                return sub, True
        new = Subscription(
            uid=uid,
            last_dynamic_id=latest_dyn_id,
            filter_types=filter_types,
            filter_regex=filter_regex,
        )
        subs.append(new)
        return new, False

    async def _process_sub(
        self,
        session: str,
        sub: Subscription,
        dyn_items: list[dict[str, Any]],
        live_info: dict[str, Any] | None,
    ) -> bool:
        """处理单个订阅：推送**最新**未读动态并更新去重锚点。"""
        changed = False
        known = {sub.last_dynamic_id, *(sub.recent_ids or [])} - {""}

        new_items = collect_new_dynamics(dyn_items, known)
        if new_items:
            # 只推送最新的 dynamic_limit 条，但**全部**标记为已读，避免重复轰炸
            for item in reversed(new_items):  # 按时间正序写入，便于 LRU 淘汰最旧的
                append_recent_id(sub, str(item.get("id_str") or ""), self.recent_ids_limit)
            sub.last_dynamic_id = str(new_items[0].get("id_str") or "")
            changed = True
            # 正则只编译一次（每次轮询每条订阅一次），避免热路径反复编译
            compiled = compile_filter_patterns(sub.filter_regex)
            for item in pick_push_batch(new_items, self.dynamic_limit):
                if should_filter_dynamic(item, sub, compiled):
                    continue
                if await self._safe_send(
                    session,
                    build_dynamic_chain(item, wrap_width=self.dynamic_wrap_width),
                    "dynamic_push",
                ):
                    self.stats["dynamic_pushes"] += 1

        # 直播状态变更
        if live_info and "live" not in (sub.filter_types or []):
            now_live = self._is_living(live_info)
            if now_live != sub.is_live:
                sub.is_live = now_live
                changed = True
                live_info.setdefault("uname", live_info.get("uname") or f"UID {sub.uid}")
                if await self._safe_send(
                    session,
                    build_live_chain(
                        live_info,
                        now_live=now_live,
                        wrap_width=self.dynamic_wrap_width,
                    ),
                    "live_push",
                ):
                    self.stats["live_pushes"] += 1
        return changed

    async def _poll_uid(
        self,
        uid: int,
        targets: list[tuple[str, Subscription]],
        sem: asyncio.Semaphore,
    ) -> bool:
        async with sem:
            dyn_res, live_res = await asyncio.gather(
                self._fetch_dynamic_items(uid),
                self._fetch_live_status(uid),
                return_exceptions=True,
            )
        # 任务间隔放在信号量之外，避免占用并发额度
        if self.uid_task_gap_seconds > 0:
            await asyncio.sleep(self.uid_task_gap_seconds)

        dyn_items: list[dict[str, Any]]
        if isinstance(dyn_res, BaseException):
            self.logger.warning("拉取动态失败 uid=%s: %s", uid, dyn_res)
            dyn_items = []
        else:
            dyn_items = dyn_res
        live: dict[str, Any] | None
        if isinstance(live_res, BaseException):
            self.logger.warning("拉取直播状态失败 uid=%s: %s", uid, live_res)
            live = None
        else:
            live = live_res

        changed = False
        for session, sub in targets:
            if await self._process_sub(session, sub, dyn_items, live):
                changed = True
        return changed

    async def _poll_once(self) -> bool:
        """执行一轮轮询，返回是否有订阅状态发生变化。"""
        # 每轮重新读取配置：设置面板改动的屏蔽列表 / 映射表无需重启即可生效
        self._refresh_config()
        added, updated = await self._apply_group_map()
        pruned = self._prune_by_group_map()
        if added or updated or pruned:
            self.logger.info(
                "群订阅映射已应用：新增 %d 条，更新 %d 条，清理 %d 条（共 %d 条映射 / %d 个会话）",
                added,
                updated,
                pruned,
                len(self.group_bindings),
                len({b.session for b in self.group_bindings}),
            )
            self._save_subscriptions()
        # 每轮都对账一次「自动同步区」：内容一致时只做一次字符串比较，开销可忽略
        self._sync_group_map_config()

        targets = build_uid_targets(self._active_subscriptions())
        if not targets:
            return False
        self.stats["poll_cycles"] += 1
        sem = asyncio.Semaphore(max(1, self.uid_concurrency))
        results = await asyncio.gather(
            *(self._poll_uid(uid, subs, sem) for uid, subs in targets.items()),
            return_exceptions=True,
        )
        changed = bool(added or updated or pruned)
        for (uid, _subs), res in zip(targets.items(), results, strict=False):
            if isinstance(res, BaseException):
                self.stats["poll_errors"] += 1
                self.logger.warning("轮询 UID %s 失败: %s", uid, res)
                continue
            changed = changed or res
        return changed

    async def _poll_loop(self) -> None:
        """轮询主循环：异常不退出，取消时正常收尾。"""
        while self._running:
            try:
                t0 = time.monotonic()
                if await self._poll_once():
                    self._save_subscriptions()
                await asyncio.sleep(max(0.0, self.poll_interval_seconds - (time.monotonic() - t0)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 轮询需永不退出
                self.stats["poll_errors"] += 1
                self.logger.error("订阅轮询异常: %s", exc)
                await asyncio.sleep(5)

    # ───────────────────── 状态自检 ─────────────────────

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """管理员判定：配置白名单 或 AstrBot 原生管理员身份。"""
        if str(event.get_sender_id()) in self.admin_ids:
            return True
        try:
            return bool(event.is_admin())
        except Exception:  # noqa: BLE001 - 兼容旧版本事件对象
            return False

    def _validate_config(self) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if self.video_quality_label not in QUALITY_TO_QN:
            errors.append(f"video_download.quality 非法: {self.video_quality_label}")
        for attr, label in (
            ("poll_interval_seconds", "subscription.poll_interval_seconds"),
            ("timeout_seconds", "subscription.timeout_seconds"),
            ("dynamic_limit", "subscription.dynamic_limit"),
            ("uid_concurrency", "subscription.uid_concurrency"),
        ):
            if getattr(self, attr, 0) <= 0:
                errors.append(f"{label} 必须 > 0")
        for attr, label in (
            ("video_max_duration_seconds", "video_download.max_duration_seconds"),
            ("video_max_size_mb", "video_download.max_size_mb"),
            ("uid_task_gap_seconds", "subscription.uid_task_gap_seconds"),
            ("empty_retry_count", "subscription.empty_retry_count"),
        ):
            if getattr(self, attr, -1) < 0:
                errors.append(f"{label} 必须 >= 0")
        qn = QUALITY_TO_QN.get(self.video_quality_label, DEFAULT_QN)
        if self.enable_video_download and qn >= 80 and not self.bili_cookie:
            warnings.append("已启用高画质下载但未填写 Cookie，可能无法获取 1080P 及以上")
        if self.bili_cookie and "SESSDATA=" not in self.bili_cookie:
            warnings.append("Cookie 中未检测到 SESSDATA，登录态可能无效")
        if self.bili_cookie and len(self.bili_cookie) > 4096:
            errors.append("video_download.bili_cookie 过长（> 4096 字符），请只粘贴 Cookie 本身")
        if self.empty_retry_count == 0:
            warnings.append(
                "subscription.empty_retry_count 为 0：B站动态接口会偶发返回空结果，建议保持 > 0 以免漏推最新动态",
            )
        if self.poll_interval_seconds < 60:
            warnings.append("轮询周期小于 60 秒，可能触发 B站风控")
        if self.uid_concurrency > 10:
            warnings.append("subscription.uid_concurrency 大于 10，并发过高容易触发风控")
        if self.min_request_interval_seconds <= 0:
            warnings.append("subscription.min_request_interval_seconds 为 0，请求无节流，建议 ≥ 0.1")
        if self.recent_ids_limit < 5:
            warnings.append("subscription.recent_ids_limit 过小，可能重复推送历史动态")
        if self.group_map_errors:
            errors.extend(f"subscription.group_subscriptions {e}" for e in self.group_map_errors[:10])
        blocked_map_sessions = sorted({b.session for b in self.group_bindings if self._is_blocked(b.session)})
        if blocked_map_sessions:
            warnings.append(
                "群订阅映射表中有 {} 个会话同时在屏蔽列表内，不会推送：{}".format(
                    len(blocked_map_sessions),
                    "、".join(blocked_map_sessions[:3]),
                )
            )
        if self.group_map_authoritative and not callable(getattr(self.config_obj, "save_config", None)):
            warnings.append(
                "subscription.group_map_authoritative 已开启，但当前配置对象不支持保存（save_config），"
                "自动同步区不会写入磁盘，仅本次运行内生效",
            )
        return errors, warnings

    async def _check_cookie(self) -> tuple[str, str]:
        payload = await self._get_json(API_NAV, {}, scene="nav")
        if not payload:
            return "fail", "请求 nav 接口失败（网络或超时）"
        code = payload.get("code")
        if code != 0:
            if self.bili_cookie:
                return "fail", f"Cookie 校验失败：nav 接口返回 code={code}，Cookie 可能已失效"
            # 匿名访问 nav 时返回 -101（未登录）属于正常现象，不应报错
            return "warn", f"未配置 Cookie（匿名模式，nav code={code}），下载画质可能受限"
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
        candidates = list(self.subscriptions.get(session, []))
        if not candidates:
            for subs in self.subscriptions.values():
                if subs:
                    candidates = list(subs)
                    break
        if candidates:
            test_uid = candidates[0].uid
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
    def _parse_uid_args(
        args: Sequence[str],
        uid_pos: int = 1,
    ) -> ParsedUidArgs | None:
        """从命令参数提取 UID 与过滤器。

        * UID 缺失/非法 → 返回 ``None``（调用方给出通用用法提示）；
        * 过滤器非法（正则过长/语法错误/条数超限）→ 返回 ``ParsedUidArgs.error``，
          调用方应把 ``error`` 原文回显给用户，而不是静默忽略。
        """
        uid_str = args[uid_pos].strip() if len(args) > uid_pos else ""
        if not is_valid_uid(uid_str):
            return None
        ft, fr = parse_filter_args(list(args[uid_pos + 1 :]), VALID_FILTER_TYPES)
        error = validate_filter_patterns(fr)
        return ParsedUidArgs(uid=int(uid_str), filter_types=ft, filter_regex=fr, error=error)

    @staticmethod
    def _fmt_filter(ft: list[str], fr: list[str]) -> str:
        parts: list[str] = []
        if ft:
            parts.append("类型过滤: " + ", ".join(ft))
        if fr:
            parts.append("正则过滤: " + ", ".join(fr))
        return ("\n" + "\n".join(parts)) if parts else ""

    @staticmethod
    def _fmt_filter_inline(sub: Subscription) -> str:
        """单行过滤器摘要，用于列表排版。"""
        parts: list[str] = []
        if sub.filter_types:
            parts.append("类型 " + "、".join(sub.filter_types))
        if sub.filter_regex:
            parts.append("正则 " + "、".join(sub.filter_regex))
        return "、".join(parts) if parts else "无"

    @staticmethod
    def _fmt_sub_line(index: int, sub: Subscription) -> str:
        """订阅列表单行排版：``1) UID 2 ｜ 状态: 直播中 ｜ 过滤: 无``。"""
        state = "直播中" if sub.is_live else "未开播"
        return f"{index}) UID {sub.uid} ｜ 状态: {state} ｜ 过滤: {BiliAllPlugin._fmt_filter_inline(sub)}"

    async def _prepare_new_subscription(
        self,
        session: str,
        uid: int,
        ft: list[str],
        fr: list[str],
    ) -> tuple[Subscription, bool]:
        """建立/更新订阅；新订阅会抓取锚点动态与初始直播状态，避免误报开播。"""
        items = await self._fetch_dynamic_items(uid)
        anchor = latest_dynamic_id(items)
        sub, exists = self._upsert_subscription(session, uid, ft, fr, anchor)
        if not exists:
            sub.is_live = self._is_living(await self._fetch_live_status(uid))
        self._save_subscriptions()
        return sub, exists

    # ───────────────────── 群屏蔽与「设置里的订阅映射」 ─────────────────────

    def _is_blocked(self, session: Any) -> bool:
        """会话是否在屏蔽列表内（被屏蔽的群完全不启用插件功能）。"""
        return is_blocked_session(session, self.blocked_groups)

    def _active_subscriptions(self) -> dict[str, list[Subscription]]:
        """参与轮询推送的订阅表：直接剔除被屏蔽会话（既不发也不请求）。"""
        if not self.blocked_groups:
            return self.subscriptions
        return {session: subs for session, subs in self.subscriptions.items() if subs and not self._is_blocked(session)}

    def _find_subscription(self, session: str, uid: int) -> Subscription | None:
        for sub in self.subscriptions.get(session, []):
            if sub.uid == uid:
                return sub
        return None

    async def _apply_group_map(self) -> tuple[int, int]:
        """把设置里的「群 = UID」映射应用到订阅表；返回 ``(新增, 更新)`` 条数。"""
        added = updated = 0
        for binding in self.group_bindings:
            if self._is_blocked(binding.session):
                continue
            existing = self._find_subscription(binding.session, binding.uid)
            if existing is None:
                if self._check_subscription_quota(binding.session, binding.uid):
                    continue
                await self._prepare_new_subscription(
                    binding.session,
                    binding.uid,
                    list(binding.filter_types),
                    list(binding.filter_regex),
                )
                added += 1
            elif not binding.same_filters(existing):
                existing.filter_types = list(binding.filter_types)
                existing.filter_regex = list(binding.filter_regex)
                updated += 1
        return added, updated

    def _prune_by_group_map(self) -> int:
        """「映射表为唯一来源」模式下，清理配置里已删除的订阅。

        仅在插件成功回写过一次配置（``_last_mirror_text`` 非空）后才会清理，
        这样首次启动、配置写保护等情况下不会误删既有订阅。
        """
        if not self.group_map_authoritative or self._last_mirror_text is None:
            return 0
        declared = {(b.session, b.uid) for b in self.group_bindings}
        removed = 0
        for session in list(self.subscriptions):
            kept = []
            for sub in self.subscriptions[session]:
                if (session, sub.uid) in declared:
                    kept.append(sub)
                else:
                    removed += 1
            if kept:
                self.subscriptions[session] = kept
            else:
                self.subscriptions.pop(session, None)
        return removed

    def _group_map_bindings(self, *, skip_blocked: bool = True) -> list[GroupBinding]:
        """当前订阅在映射表里的等价表示（按会话、UID 排序，保证文本稳定）。"""
        out = [
            GroupBinding(
                session=session,
                uid=sub.uid,
                filter_types=list(sub.filter_types),
                filter_regex=list(sub.filter_regex),
            )
            for session, subs in self.subscriptions.items()
            for sub in subs
            if not (skip_blocked and self._is_blocked(session))
        ]
        out.sort(key=lambda b: (b.session, b.uid))
        return out

    def _sync_group_map_config(self, *, force: bool = False) -> bool:
        """把当前订阅回写进配置的「自动同步区」。

        这样「旧方法」（群内 ``/bili_sub`` 建立的订阅）也会出现在设置面板里，
        管理员可以在页面上直接查看、编辑或复制这些映射。管理员手写区已经声明过的
        映射不会在自动同步区里重复出现（手写区优先级更高）。
        """
        if self.config_obj is None:
            return False
        if not self.sync_group_map and not force:
            return False
        admin_text, _auto_text = split_group_map(self.group_map_text)
        declared = {(b.session, b.uid) for b in parse_group_map(admin_text)}
        entries = [b for b in self._group_map_bindings() if (b.session, b.uid) not in declared]
        new_text = render_group_map(admin_text, entries)
        if new_text == self.group_map_text:
            self._last_mirror_text = new_text
            return False
        if not self._set_cfg("subscription.group_subscriptions", new_text):
            return False
        self.group_map_text = new_text
        self._last_mirror_text = new_text
        self.group_bindings = tuple(parse_group_map(new_text))
        self.group_map_errors = validate_group_map(new_text)
        self._persist_config()
        return True

    # ───────────────────── 指令：自检 ─────────────────────

    @filter.command("bili_state")
    @_blocked_guard
    async def cmd_bili_state(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """插件状态自检。"""
        self._refresh_config()
        errors, warnings = self._validate_config()
        lines: list[str] = [f"bili_state 自检结果（v{PLUGIN_VERSION} / {PLUGIN_DISPLAY_NAME}）"]

        if errors:
            lines.append("\n[异常] 配置检查未通过：")
            lines.extend(f"- {e}" for e in errors)
            if warnings:
                lines.append("\n[警告] 配置警告：")
                lines.extend(f"- {w}" for w in warnings)
            lines.append("\n请先修复配置后再执行 /bili_state。")
            yield event.plain_result("\n".join(lines))
            return

        lines.append("\n[正常] 配置检查通过")
        if warnings:
            lines.append("[警告] 配置警告：")
            lines.extend(f"- {w}" for w in warnings)

        cookie_status, cookie_msg = await self._check_cookie()
        lines.append(f"\n{_status_icon(cookie_status)} Cookie检查：{cookie_msg}")
        sub_status, sub_msg = await self._check_subscription(self._session_key(event))
        lines.append(f"{_status_icon(sub_status)} 订阅检查：{sub_msg}")

        lines.append("\n功能开关：")
        lines.extend(
            [
                f"- 链接解析: {'开启' if self.enable_bili_parse else '关闭'}",
                f"- 视频信息回复: {'开启' if self.send_video_info else '关闭'}",
                f"- 下载发送: {'开启' if self.enable_video_download else '关闭'}",
                f"- 下载画质: {self.video_quality_label}",
                f"- LLM 工具: {'开启' if self.enable_llm_tool else '关闭'}",
                f"- 时长上限: {'不限制' if self.video_max_duration_seconds == 0 else f'{self.video_max_duration_seconds}s'}",
                f"- 大小上限: {'不限制' if self.video_max_size_mb == 0 else f'{self.video_max_size_mb}MB'}",
                f"- 自动删缓存: {'是' if self.video_auto_delete else '否'}",
            ]
        )

        lines.append("\n轮询策略：")
        lines.extend(
            [
                "- 轮询重试: 0（零重试，避免轮询内堆积）",
                f"- 空响应重试: {self.empty_retry_count}（间隔 {self.empty_retry_delay_seconds}s 递增）",
                f"- UID 并发: {self.uid_concurrency}",
                f"- 请求节流: {self.min_request_interval_seconds}s/次",
                f"- 解析/自检重试: {self.http_retry_count}",
                f"- 下载重试: {self.video_retry_count}",
                f"- 重试间隔: {self.http_retry_delay_seconds}s × 次数",
            ]
        )

        map_sessions = len({b.session for b in self.group_bindings})
        session_key = self._session_key(event)
        session_mapped = sum(1 for b in self.group_bindings if b.session == session_key)
        lines.append("\n群聊开关（设置面板）：")
        lines.extend(
            [
                f"- 屏蔽群聊: {len(self.blocked_groups)} 个",
                f"- 本会话映射: {session_mapped} 条",
                f"- 配置映射: {len(self.group_bindings)} 条 / {map_sessions} 个会话",
                f"- 映射同步模式: {'以配置为准（删除即取消订阅）' if self.group_map_authoritative else '增量添加'}",
                f"- 自动同步区回写: {'已启用' if self.sync_group_map else '已关闭'}"
                f"（{'已回写' if self._last_mirror_text is not None else '未回写'}）",
            ]
        )

        snap = self.health_snapshot()
        stats = snap["stats"]
        lines.append("\n运行统计：")
        lines.extend(
            [
                f"- 已运行: {snap['uptime_seconds']}s（轮询轮次 {stats['poll_cycles']}）",
                f"- 请求: {stats['http_requests']} 次（失败 {stats['http_failures']}，静默降级 {stats['http_silent']}）",
                f"- 推送: 动态 {stats['dynamic_pushes']} 条 / 直播 {stats['live_pushes']} 次",
                f"- 下载: {stats['downloads']} 个（{format_bytes(stats['download_bytes'])}）",
                f"- 发送失败: {stats['send_failures']} 次，轮询异常: {stats['poll_errors']} 次",
                f"- 登录态: {snap['cookie_masked'] or '未配置'}",
                f"- 数据版本: {snap['schema_version']}",
            ]
        )

        final = (
            "fail"
            if "fail" in (cookie_status, sub_status)
            else ("warn" if "warn" in (cookie_status, sub_status) or warnings else "ok")
        )
        tail = {
            "ok": "\n自检通过：核心功能可用。",
            "warn": "\n自检完成：存在警告，请按提示优化配置。",
        }
        lines.append(tail.get(final, "\n自检完成：存在失败项，请按提示修复。"))
        yield event.plain_result("\n".join(lines))

    # ───────────────────── 指令：订阅管理 ─────────────────────

    @filter.command("bili_sub")
    @_blocked_guard
    async def cmd_bili_sub(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """订阅UP主动态。用法：/bili_sub <UID> [过滤器...]"""
        parsed = self._parse_uid_args((event.message_str or "").split())
        if not parsed:
            yield event.plain_result("参数错误。用法：/bili_sub <B站UID> [过滤器...]")
            return
        if parsed.error:
            yield event.plain_result(f"过滤器无效：{parsed.error}")
            return
        uid, ft, fr = parsed.uid, parsed.filter_types, parsed.filter_regex
        session = self._session_key(event)
        limit_msg = self._check_subscription_quota(session, uid)
        if limit_msg:
            yield event.plain_result(limit_msg)
            return
        _, exists = await self._prepare_new_subscription(session, uid, ft, fr)
        self._sync_group_map_config()
        detail = self._fmt_filter(ft, fr)
        if exists:
            yield event.plain_result(f"该 UID 已订阅，已更新过滤条件：{uid}{detail}")
        else:
            yield event.plain_result(
                f"订阅成功：UID {uid}{detail}\n新的动态将由后台轮询推送。\n"
                "该映射已同步到插件设置的「群订阅映射表」，也可在设置面板直接查看/编辑。"
            )

    def _check_subscription_quota(self, session: str, uid: int) -> str:
        """检查订阅配额，超限时返回提示文案（未超限返回空串）。"""
        subs = self.subscriptions.get(session, [])
        if any(s.uid == uid for s in subs):
            return ""
        if len(subs) >= MAX_SUBS_PER_SESSION:
            return f"订阅已达上限（单会话最多 {MAX_SUBS_PER_SESSION} 个），请先取消部分订阅。"
        if session not in self.subscriptions and len(self.subscriptions) >= MAX_SESSIONS:
            return f"会话数已达上限（{MAX_SESSIONS}），请联系管理员清理。"
        return ""

    @filter.command("bili_sub_del")
    @_blocked_guard
    async def cmd_bili_sub_del(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """取消订阅。用法：/bili_sub_del <UID>"""
        args = (event.message_str or "").split()
        uid_str = args[1].strip() if len(args) >= 2 else ""
        if not is_valid_uid(uid_str):
            yield event.plain_result("参数错误。用法：/bili_sub_del <B站UID>")
            return
        session = self._session_key(event)
        uid = int(uid_str)
        if not self._remove_subscription(session, uid):
            yield event.plain_result("未找到该订阅。")
            return
        self._save_subscriptions()
        self._sync_group_map_config()
        yield event.plain_result(f"已取消订阅 UID {uid}")

    def _remove_subscription(self, session: str, uid: int) -> bool:
        """删除指定会话下的订阅，返回是否真的删除了。"""
        old = self.subscriptions.get(session, [])
        new = [s for s in old if s.uid != uid]
        if len(new) == len(old):
            return False
        if new:
            self.subscriptions[session] = new
        else:
            self.subscriptions.pop(session, None)
        return True

    @filter.command("bili_sub_list")
    @_blocked_guard
    async def cmd_bili_sub_list(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """查看当前会话订阅列表。"""
        subs = self.subscriptions.get(self._session_key(event), [])
        if not subs:
            yield event.plain_result("当前会话没有订阅。使用 /bili_sub <UID> 添加。")
            return
        lines = [f"当前会话订阅列表（共 {len(subs)} 个）："]
        lines.extend(self._fmt_sub_line(i, s) for i, s in enumerate(subs, 1))
        yield event.plain_result("\n".join(lines))

    @filter.command("bili_sub_test")
    @_blocked_guard
    async def cmd_bili_sub_test(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """测试动态抓取与过滤。用法：/bili_sub_test <UID> [过滤器...]"""
        parsed = self._parse_uid_args((event.message_str or "").split())
        if not parsed:
            yield event.plain_result("参数错误。用法：/bili_sub_test <B站UID> [过滤器...]")
            return
        if parsed.error:
            yield event.plain_result(f"过滤器无效：{parsed.error}")
            return
        uid, ft, fr = parsed.uid, parsed.filter_types, parsed.filter_regex
        test_sub = Subscription(uid=uid, filter_types=ft, filter_regex=fr)
        items = await self._fetch_dynamic_items(uid)
        if not items:
            yield event.plain_result(
                "未获取到动态数据（B站接口偶发返回空结果），请稍后重试。",
            )
            return
        candidates = collect_new_dynamics(items, set())
        compiled = compile_filter_patterns(fr)
        for item in candidates:
            if should_filter_dynamic(item, test_sub, compiled):
                continue
            chain = build_dynamic_chain(item, wrap_width=self.dynamic_wrap_width)
            detail = self._fmt_filter(ft, fr)
            if detail:
                chain.append(Comp.Plain(f"\n测试过滤条件: {detail.strip()}"))
            yield event.chain_result(chain)
            return
        yield event.plain_result("测试完成：最新动态都被当前过滤条件过滤掉了。")

    # ───────────────────── 指令：管理员 ─────────────────────

    @filter.command("bili_global_sub")
    @_blocked_guard
    async def cmd_bili_global_sub(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """管理员全局订阅。用法：/bili_global_sub <会话UMO> <UID> [过滤器...]"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        args = (event.message_str or "").split()
        session = normalize_session(args[1]) if len(args) >= 3 else ""
        parsed = self._parse_uid_args(args, uid_pos=2) if len(args) >= 3 else None
        if not session or not parsed:
            yield event.plain_result(
                "参数错误。用法：/bili_global_sub <会话UMO|群号> <B站UID> [过滤器...]",
            )
            return
        if parsed.error:
            yield event.plain_result(f"过滤器无效：{parsed.error}")
            return
        uid, ft, fr = parsed.uid, parsed.filter_types, parsed.filter_regex
        limit_msg = self._check_subscription_quota(session, uid)
        if limit_msg:
            yield event.plain_result(limit_msg)
            return
        _, exists = await self._prepare_new_subscription(session, uid, ft, fr)
        self._sync_group_map_config()
        yield event.plain_result(
            f"{'已更新' if exists else '已添加'}全局订阅：{session} -> UID {uid}",
        )

    @filter.command("bili_global_del")
    @_blocked_guard
    async def cmd_bili_global_del(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """管理员删除全局订阅。用法：/bili_global_del <会话UMO> [UID]"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        args = (event.message_str or "").split()
        session = normalize_session(args[1]) if len(args) >= 2 else ""
        if not session:
            yield event.plain_result("参数错误。用法：/bili_global_del <会话UMO|群号> [B站UID]")
            return
        uid_str = args[2].strip() if len(args) >= 3 else ""
        if not uid_str:
            if session in self.subscriptions:
                self.subscriptions.pop(session)
                self._save_subscriptions()
                self._sync_group_map_config()
                yield event.plain_result(f"已删除会话 {session} 的全部订阅。")
            else:
                yield event.plain_result("未找到该会话的订阅。")
            return
        if not uid_str.isdigit():
            yield event.plain_result("UID 必须是数字。")
            return
        uid = int(uid_str)
        if not self._remove_subscription(session, uid):
            yield event.plain_result("未找到指定订阅。")
            return
        self._save_subscriptions()
        self._sync_group_map_config()
        yield event.plain_result(f"已删除全局订阅：{session} -> UID {uid}")

    @filter.command("bili_map_sync")
    @_blocked_guard
    async def cmd_bili_map_sync(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """管理员立即应用「群订阅映射表」。用法：/bili_map_sync"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        self._refresh_config()
        added, updated = await self._apply_group_map()
        pruned = self._prune_by_group_map()
        if added or updated or pruned:
            self._save_subscriptions()
        mirrored = self._sync_group_map_config(force=True)
        sessions = len({b.session for b in self.group_bindings})
        lines = [
            "群订阅映射同步完成：",
            f"- 配置映射: {len(self.group_bindings)} 条 / {sessions} 个会话",
            f"- 本次新增: {added} 条，更新: {updated} 条，清理: {pruned} 条",
            f"- 自动同步区回写: {'已更新' if mirrored else '无需更新'}",
            f"- 屏蔽群聊: {len(self.blocked_groups)} 个",
        ]
        if self.group_map_errors:
            lines.append("- 映射表问题:")
            lines.extend(f"  - {err}" for err in self.group_map_errors[:5])
        yield event.plain_result("\n".join(lines))

    @filter.command("bili_global_list")
    @_blocked_guard
    async def cmd_bili_global_list(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """管理员查看全局订阅。"""
        if not self._is_admin(event):
            yield event.plain_result("无权限：仅管理员可使用该指令。")
            return
        if not self.subscriptions:
            yield event.plain_result("当前没有任何订阅。")
            return
        lines: list[str] = [
            f"全局订阅列表（{len(self.subscriptions)} 个会话 / {self._sub_count()} 个订阅）：",
        ]
        for session, subs in self.subscriptions.items():
            if not subs:
                continue
            mark = "（已屏蔽，不推送）" if self._is_blocked(session) else ""
            lines.append(f"▸ {session}{mark}")
            lines.extend(f"  {self._fmt_sub_line(i, s)}" for i, s in enumerate(subs, 1))
        lines.append("\n以上订阅均已同步到插件设置「群订阅映射表」，可在设置面板直接编辑。")
        preview = [
            format_group_map_line(b.session, b.uid, b.filter_types, b.filter_regex) for b in self._group_map_bindings()
        ]
        lines.append("映射行（可直接粘贴进设置面板）:")
        lines.extend(f"  {line}" for line in preview[:10])
        if len(preview) > 10:
            lines.append(f"  ...（共 {len(preview)} 行）")
        yield event.plain_result("\n".join(lines))

    # ───────────────────── 消息事件：链接解析 ─────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def handle_bili_parse(self, event: AstrMessageEvent) -> None:
        """统一解析普通文本与 QQ 小程序 JSON 卡片中的 B站视频链接。"""
        if not self.enable_bili_parse or event.get_sender_id() == event.get_self_id():
            return
        # 被屏蔽的群不启用任何功能（不解析、不回复，也不阻断事件给其它插件）
        if self._is_blocked(self._session_key(event)):
            return

        ref: dict[str, Any] | None = None
        matched = False
        # 超长消息（刷屏/粘贴日志）不参与扫描，避免拖慢事件处理
        message_text = str(event.message_str or "")[:MAX_MESSAGE_SCAN_LEN]

        # 1. 纯文本提取
        tokens = extract_links_and_ids(message_text)
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
                    self.logger.warning("从 QQ 小程序卡片提取到 B站链接但无法解析: %s", bili_links)

        if matched:
            event.stop_event()
        if not ref or not (self.send_video_info or self.enable_video_download):
            return

        info = await self._get_video_info(ref, retry_count=self.video_retry_count)
        if info and self.send_video_info:
            await self._safe_send(
                event.unified_msg_origin,
                build_video_chain(info, ref, wrap_width=self.dynamic_wrap_width),
                "video_info",
            )
        await self._send_video_download(event, ref, info)

    # ───────────────────── LLM 函数工具 ─────────────────────

    @filter.llm_tool(name="bili_video_info")
    async def llm_bili_video_info(
        self,
        event: AstrMessageEvent,
        url: str,
    ) -> str:
        """解析 B 站视频链接、BV 号或 av 号，返回视频标题、UP主、播放量、时长等信息。

        Args:
            url(string): B 站视频链接、BV 号或 av 号
        """
        if not self.enable_llm_tool:
            return "B站链接解析工具当前已被插件配置关闭。"
        if self._is_blocked(self._session_key(event)):
            return "当前群聊已停用B站插件功能，无法使用该工具。"
        raw = str(url or "").strip()
        if len(raw) > MAX_LLM_URL_LEN:
            return f"输入过长（>{MAX_LLM_URL_LEN} 字符），请只提供 B站视频链接、BV 号或 av 号。"
        tokens = extract_links_and_ids(raw)
        if not tokens and raw:
            tokens = [raw]
        ref = await self._extract_video_ref_from_tokens(tokens)
        if not ref:
            return "未能识别 B站视频标识：请提供 bilibili.com/video 链接、BV 号或 av 号。"
        info = await self._get_video_info(ref, retry_count=self.video_retry_count)
        if not info:
            return "视频信息获取失败，请稍后重试。"
        return build_video_text(info, ref, wrap_width=self.dynamic_wrap_width)
