"""手动联调脚本：直接访问 B站 真实接口，验证动态订阅链路与推送排版。

用法（需要网络）：

    python scripts/live_check.py [UID ...]

脚本只做只读请求，不会写入订阅数据；发送动作会被打印到控制台。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as plugin_main  # noqa: E402
from bili_helpers import dynamic_author_name, dynamic_pub_ts, is_top_dynamic  # noqa: E402
from bili_logic import (  # noqa: E402
    build_live_chain,
    collect_new_dynamics,
    latest_dynamic_id,
    pick_push_batch,
)
from bili_models import Subscription  # noqa: E402

DEFAULT_UIDS = (703007996, 672328094, 946974)


class RecordingContext:
    """记录主动发送内容的最小 Context 替身。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, list[Any]]] = []

    async def send_message(self, session: str, chain: Any) -> bool:
        self.sent.append((session, list(getattr(chain, "chain", chain))))
        return True


def render(chain: list[Any]) -> str:
    out: list[str] = []
    for comp in chain:
        text = getattr(comp, "text", None)
        if text is not None:
            out.append(str(text))
        else:
            out.append(f"<{type(comp).__name__}: {getattr(comp, 'url', '')}>")
    return "\n".join(out)


async def check_uid(plugin: plugin_main.BiliAllPlugin, uid: int) -> None:
    print(f"\n{'=' * 72}\nUID {uid}\n{'=' * 72}")
    items = await plugin._fetch_dynamic_items(uid)
    if not items:
        print("  !! 动态接口未返回数据（风控或接口变更）")
        return
    print(f"  共 {len(items)} 条动态，前 5 条：")
    for item in items[:5]:
        print(
            f"    - id={item.get('id_str')} type={item.get('type'):24s} "
            f"top={is_top_dynamic(item)} author={dynamic_author_name(item)!r} "
            f"ts={dynamic_pub_ts(item)}",
        )

    anchor = latest_dynamic_id(items)
    pinned = items[0] if is_top_dynamic(items[0]) else None
    newest = items[1] if pinned and len(items) > 1 else items[0]
    print(f"  置顶动态: {pinned.get('id_str') if pinned else '无'}")
    print(f"  订阅锚点（最新非置顶动态）: {anchor}")
    assert anchor == newest.get("id_str"), "锚点必须是最新的非置顶动态"

    # 场景 1：锚点即最新动态 → 不应推送任何内容
    sub = Subscription(uid=uid, last_dynamic_id=anchor)
    stale = collect_new_dynamics(items, {sub.last_dynamic_id})
    print(f"  场景1（已读全部）未读动态数: {len(stale)}（应为 0）")
    assert stale == []

    # 场景 2：把锚点后退若干条，模拟「漏了几条」→ 必须推送最新的
    older_anchor = str(items[3].get("id_str") or "") if len(items) > 3 else ""
    sub2 = Subscription(uid=uid, last_dynamic_id=older_anchor)
    ctx = RecordingContext()
    plugin.context = ctx  # type: ignore[assignment]

    new_items = collect_new_dynamics(items, {sub2.last_dynamic_id})
    batch = pick_push_batch(new_items, plugin.dynamic_limit)
    print(f"  场景2：未读 {len(new_items)} 条，推送 {len(batch)} 条（应为最新 {plugin.dynamic_limit} 条）")
    print(f"        推送 id 顺序: {[i.get('id_str') for i in batch]}")
    print(f"        最新动态 id  : {newest.get('id_str')}")
    assert batch and batch[-1].get("id_str") == newest.get("id_str"), "推送批次必须包含最新动态"

    await plugin._process_sub("live:test:session", sub2, items, None)
    print(f"  实际发送 {len(ctx.sent)} 条消息，最后一条排版预览：")
    if ctx.sent:
        print("  " + render(ctx.sent[-1][1]).replace("\n", "\n  "))

    live = await plugin._fetch_live_status(uid)
    print(f"  直播状态: {live.get('uname') if live else None} live={plugin._is_living(live)}")

    # 直播推送渲染
    if live:
        print("  直播推送排版预览：")
        print("  " + render(build_live_chain(live, now_live=True)).replace("\n", "\n  "))


async def main() -> None:
    uids = [int(x) for x in sys.argv[1:]] or list(DEFAULT_UIDS)
    plugin = plugin_main.BiliAllPlugin(context=RecordingContext(), config={})
    plugin.poll_interval_seconds = 1
    try:
        for uid in uids:
            await check_uid(plugin, uid)
    finally:
        if plugin._http_session and not plugin._http_session.closed:
            await plugin._http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
