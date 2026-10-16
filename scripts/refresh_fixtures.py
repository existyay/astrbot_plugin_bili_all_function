"""刷新 ``tests/fixtures`` 中的接口快照（需要网络，仅供维护者使用）。

用法::

    python scripts/refresh_fixtures.py

脚本会只读访问 B站公开接口，裁掉与插件逻辑无关的大字段后写入测试夹具，
使单元测试可以完全离线运行且结构贴近线上真实返回。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aiohttp  # noqa: E402

from bili_constants import (  # noqa: E402
    API_DYNAMIC_FEED,
    API_LIVE_STATUS,
    API_NAV,
    API_PLAYURL,
    API_VIDEO_VIEW,
    DYNAMIC_SPACE_URL,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures"
UIDS = {
    "asoul": 703007996,
    "jiaran": 672328094,
    "yingshi": 946974,
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
AUTHOR_KEYS = ("name", "mid", "pub_ts", "pub_text", "is_top")
ARCHIVE_KEYS = (
    "aid",
    "bvid",
    "cover",
    "desc",
    "duration_text",
    "duration",
    "title",
    "stat",
    "jump_url",
    "badge",
)
STAT_KEYS = ("play", "danmaku", "comment", "like", "coin", "favorite", "forward")
NODE_KEYS = ("type", "text", "orig_text", "rid", "jump_url")
PLACEHOLDER_STREAM = "https://upos-sz-mirrorcos.bilivideo.com/fixture/video.m4s"


def _headers(uid: int | None = None) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://www.bilibili.com",
        "Referer": DYNAMIC_SPACE_URL.format(uid=uid) if uid else "https://www.bilibili.com/",
    }


async def get_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, Any],
    uid: int | None = None,
) -> dict[str, Any]:
    async with session.get(url, params=params, headers=_headers(uid), timeout=aiohttp.ClientTimeout(total=20)) as resp:
        return json.loads(await resp.text())


def pick(obj: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    return {k: obj[k] for k in keys if k in obj and obj[k] is not None}


def trim_stat(stat: Any) -> dict[str, Any]:
    return pick(stat, STAT_KEYS)


def trim_nodes(nodes: Any) -> list[dict[str, Any]]:
    if not isinstance(nodes, list):
        return []
    out = []
    for node in nodes:
        if isinstance(node, dict):
            item = pick(node, NODE_KEYS)
            if item:
                out.append(item)
    return out


def trim_item(item: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id_str": item.get("id_str"),
        "type": item.get("type"),
        "visible": item.get("visible", True),
        "basic": pick(item.get("basic"), ("rid_str", "rtype", "is_top")),
    }
    modules: list[dict[str, Any]] = []
    raw_modules = item.get("modules")
    entries = raw_modules if isinstance(raw_modules, list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mtype = entry.get("module_type")
        if mtype == "MODULE_TYPE_AUTHOR":
            author = pick(entry.get("module_author"), AUTHOR_KEYS)
            user = entry.get("module_author", {}).get("user")
            if isinstance(user, dict):
                author["user"] = pick(user, ("name", "mid", "face"))
            modules.append({"module_type": mtype, "module_author": author})
        elif mtype == "MODULE_TYPE_DESC":
            desc = pick(entry.get("module_desc"), ("text",))
            desc["rich_text_nodes"] = trim_nodes(entry.get("module_desc", {}).get("rich_text_nodes"))
            modules.append({"module_type": mtype, "module_desc": desc})
        elif mtype == "MODULE_TYPE_DYNAMIC":
            dynamic = entry.get("module_dynamic") or {}
            trimmed: dict[str, Any] = {"type": dynamic.get("type")}
            if isinstance(dynamic.get("desc"), dict):
                trimmed["desc"] = pick(dynamic["desc"], ("text",))
            draw = dynamic.get("dyn_draw")
            if isinstance(draw, dict):
                trimmed["dyn_draw"] = {
                    "items": [
                        pick(d, ("src", "width", "height")) for d in (draw.get("items") or []) if isinstance(d, dict)
                    ],
                }
            archive = dynamic.get("dyn_archive")
            if isinstance(archive, dict):
                trimmed_archive = pick(archive, ARCHIVE_KEYS)
                trimmed_archive["stat"] = trim_stat(archive.get("stat"))
                trimmed["dyn_archive"] = trimmed_archive
            forward = dynamic.get("dyn_forward")
            if isinstance(forward, dict) and isinstance(forward.get("item"), dict) and depth < 1:
                trimmed["dyn_forward"] = {
                    "type": forward.get("type"),
                    "item": trim_item(forward["item"], depth + 1),
                }
            modules.append({"module_type": mtype, "module_dynamic": trimmed})
        elif mtype == "MODULE_TYPE_STAT":
            modules.append({"module_type": mtype, "module_stat": trim_stat(entry.get("module_stat"))})
        elif mtype == "MODULE_TYPE_TAG":
            modules.append({"module_type": mtype, "module_tag": pick(entry.get("module_tag"), ("text",))})
    out["modules"] = modules
    if depth == 0 and item.get("orig") is not None:
        out["orig"] = trim_item(item["orig"], depth + 1)
    return out


async def fetch_feed(session: aiohttp.ClientSession, uid: int, tries: int = 15) -> dict[str, Any]:
    """动态接口会偶发返回空列表，这里重试直到拿到数据。"""
    for attempt in range(tries):
        payload = await get_json(
            session,
            API_DYNAMIC_FEED,
            {"host_mid": uid, "timezone_offset": -480, "offset": ""},
            uid=uid,
        )
        items = (payload.get("data") or {}).get("items") or []
        if items:
            payload["data"]["items"] = [trim_item(it) for it in items]
            payload["data"].pop("update_baseline", None)
            return {
                "code": payload.get("code"),
                "message": payload.get("message"),
                "data": {
                    "has_more": payload["data"].get("has_more"),
                    "offset": payload["data"].get("offset"),
                    "items": payload["data"]["items"],
                },
                "_fixture": {"uid": uid, "attempts": attempt + 1},
            }
        await asyncio.sleep(0.8)
    raise RuntimeError(f"UID {uid} 动态接口始终为空")


async def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession(trust_env=True) as session:
        for tag, uid in UIDS.items():
            feed = await fetch_feed(session, uid)
            path = FIXTURE_DIR / f"dynamic_feed_{tag}.json"
            path.write_text(json.dumps(feed, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{path.relative_to(ROOT)}: {len(feed['data']['items'])} items")

        # 用快照里的真实 bvid 拉取视频信息 / 播放地址
        bvid = None
        for tag in UIDS:
            feed = json.loads((FIXTURE_DIR / f"dynamic_feed_{tag}.json").read_text(encoding="utf-8"))
            for item in feed["data"]["items"]:
                for module in item["modules"]:
                    bvid = ((module.get("module_dynamic") or {}).get("dyn_archive") or {}).get("bvid")
                    if bvid:
                        break
                if bvid:
                    break
            if bvid:
                break
        assert bvid, "未在动态快照中找到 bvid"

        view = await get_json(session, API_VIDEO_VIEW, {"bvid": bvid})
        (FIXTURE_DIR / "video_view.json").write_text(
            json.dumps(view, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"video_view.json: code={view.get('code')} bvid={bvid}")

        pages = (view.get("data") or {}).get("pages") or [{}]
        cid = pages[0].get("cid")
        playurl = await get_json(
            session,
            API_PLAYURL,
            {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 0, "fnver": 0, "fourk": 1},
        )
        data = playurl.get("data") or {}
        durl = data.get("durl") or []
        for row in durl:
            if isinstance(row, dict):
                row["url"] = PLACEHOLDER_STREAM
                row.pop("backup_url", None)
        if isinstance(data.get("dash"), dict):
            data["dash"] = {"video": [{"id": 64, "baseUrl": PLACEHOLDER_STREAM}]}
            data.pop("dash", None) if not durl else None
        (FIXTURE_DIR / "playurl.json").write_text(
            json.dumps(
                {"code": playurl.get("code"), "message": playurl.get("message"), "data": data},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"playurl.json: code={playurl.get('code')} durl={len(durl)}")

        nav = await get_json(session, API_NAV, {})
        (FIXTURE_DIR / "nav_anonymous.json").write_text(
            json.dumps(
                {
                    "code": nav.get("code"),
                    "message": nav.get("message"),
                    "data": {"isLogin": bool((nav.get("data") or {}).get("isLogin")), "uname": ""},
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"nav_anonymous.json: code={nav.get('code')}")

        status = await get_json(
            session,
            API_LIVE_STATUS,
            {"uids[]": UIDS["jiaran"]},
        )
        row = ((status.get("data") or {}) or {}).get(str(UIDS["jiaran"])) or {}
        trimmed_row = pick(row, ("uid", "uname", "room_id", "title", "live_status", "cover", "keyframe"))
        (FIXTURE_DIR / "live_status.json").write_text(
            json.dumps(
                {
                    "code": status.get("code"),
                    "message": status.get("message"),
                    "data": {str(UIDS["jiaran"]): trimmed_row},
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"live_status.json: {trimmed_row.get('uname')} live={trimmed_row.get('live_status')}")


if __name__ == "__main__":
    asyncio.run(main())
