"""``bili_logic`` 业务逻辑测试（含「最新动态漏推」回归用例）。"""

from __future__ import annotations

from conftest import chain_text, count_images, feed_items

import bili_helpers as H
import bili_logic as L
from bili_models import Subscription


def normalized(name: str) -> list[dict]:
    items = feed_items(name)
    for item in items:
        H.normalize_modules(item)
    return items


def item(*, id_str: str = "1", type_: str = "DYNAMIC_TYPE_WORD", **modules) -> dict:
    mods: dict = {}
    if "author" in modules:
        mods["module_author"] = modules["author"]
    if "desc" in modules:
        mods["module_dynamic"] = {"desc": {"text": modules["desc"]}}
    if "dynamic" in modules:
        mods.setdefault("module_dynamic", {}).update(modules["dynamic"])
    if "tag" in modules:
        mods["module_tag"] = {"text": modules["tag"]}
    return {"id_str": id_str, "type": type_, "modules": mods}


# ───────────────────────── 视频消息链 ─────────────────────────


class TestBuildVideo:
    INFO = {
        "bvid": "BV1xx411c7mD",
        "title": "标题",
        "desc": "简介" * 100,
        "duration": 100,
        "pic": "https://i0.hdslb.com/cover.jpg",
        "owner": {"name": "UP主"},
        "stat": {"view": 25000, "like": 1234},
        "pages": [
            {"cid": 1, "part": "P1", "duration": 60},
            {"cid": 2, "part": "P2", "duration": 40},
        ],
    }

    def test_text_multi_page(self):
        text = L.build_video_text(self.INFO, {"bvid": "BV1xx411c7mD", "page": 1})
        assert "标题" in text
        assert "UP: UP主" in text
        assert "播放: 2.5万" in text
        assert "点赞: 1234" in text
        assert "时长: 0:40" in text  # 第二分P时长
        assert "分P: 2/2 P2" in text
        assert "简介: " in text and "..." in text
        assert text.rstrip().endswith("https://www.bilibili.com/video/BV1xx411c7mD")

    def test_text_minimal(self):
        text = L.build_video_text({}, {"aid": 2})
        assert "未知标题" in text
        assert "UP: 未知UP" in text
        assert "播放: 0" in text
        assert "分P" not in text
        assert "https://www.bilibili.com/video/" not in text

    def test_text_uses_ref_bvid(self):
        text = L.build_video_text({"title": "t"}, {"bvid": "BV1zz411c7mD"})
        assert "BV1zz411c7mD" in text

    def test_chain_with_and_without_cover(self):
        chain = L.build_video_chain(self.INFO, {"bvid": "BV1xx411c7mD"})
        assert count_images(chain) == 1
        assert chain_text(chain).startswith("标题")
        assert count_images(L.build_video_chain({"title": "t", "pic": ""}, {})) == 0

    def test_bad_stat_and_owner_types(self):
        text = L.build_video_text({"stat": "bad", "owner": "bad", "duration": "1:30"}, {})
        assert "播放: 0" in text
        assert "时长: 1:30" in text


# ───────────────────────── 动态消息链 ─────────────────────────


class TestBuildDynamicChain:
    def test_asoul_draw_layout(self):
        items = normalized("dynamic_feed_asoul.json")
        newest = next(it for it in items if not H.is_top_dynamic(it))
        chain = L.build_dynamic_chain(newest)
        text = chain_text(chain)
        assert text.startswith("[B站动态] A-SOUL_Official · 图文动态")
        assert count_images(chain) >= 1
        assert f"🔗 https://t.bilibili.com/{newest['id_str']}" in text
        # 排版：正文 → 图片 → 链接
        kinds = [type(c).__name__ for c in chain]
        assert kinds[0] == "Plain"
        assert kinds[-1] == "Plain"
        assert "Image" in kinds

    def test_jiaran_archive_layout(self):
        items = normalized("dynamic_feed_jiaran.json")
        archive_items = [it for it in items if H.dynamic_archive(it)]
        assert archive_items
        item_ = archive_items[0]
        chain = L.build_dynamic_chain(item_)
        text = chain_text(chain)
        assert "[B站动态] 嘉然今天吃什么 · 视频动态" in text
        bvid = H.dynamic_archive(item_)["bvid"]
        assert f"🔗 https://www.bilibili.com/video/{bvid}" in text
        assert f"🔗 https://t.bilibili.com/{item_['id_str']}" in text
        assert "播放 " in text and "弹幕 " in text and "时长 " in text
        assert count_images(chain) == 1  # 视频封面

    def test_forward_includes_orig_text(self):
        items = normalized("dynamic_feed_yingshi.json")
        forwards = [it for it in items if H.dynamic_forward_orig(it)]
        assert forwards
        text = chain_text(L.build_dynamic_chain(forwards[0]))
        assert "转发动态" in text
        assert "@" in text

    def test_desc_only_pinned_keeps_text(self):
        """回归：只有 MODULE_TYPE_DESC 的动态不能丢正文（旧实现会推送空内容）。"""
        items = normalized("dynamic_feed_asoul.json")
        pinned = items[0]
        assert H.is_top_dynamic(pinned)
        text = chain_text(L.build_dynamic_chain(pinned))
        assert text.startswith("[B站动态] A-SOUL_Official · 图文动态")
        assert len(text) > 40

    def test_counts_do_not_crash_on_chinese_units(self):
        """回归：``int("2.5万")`` 曾直接抛 ValueError。"""
        items = normalized("dynamic_feed_jiaran.json")
        chain = L.build_dynamic_chain(items[1])
        assert "万" in chain_text(chain) or "播放 " in chain_text(chain)

    def test_empty_item(self):
        chain = L.build_dynamic_chain({"id_str": "", "type": "DYNAMIC_TYPE_WORD", "modules": {}})
        assert chain_text(chain) == "[B站动态] UP主 · 文字动态"

    def test_non_dict_item_is_tolerated(self):
        assert chain_text(L.build_dynamic_chain("bad")) == "[B站动态] UP主 · 动态"

    def test_archive_without_duration_text(self):
        it = item(
            id_str="5",
            type_="DYNAMIC_TYPE_AV",
            dynamic={"major": {"archive": {"bvid": "BV1", "stat": {"play": 12}, "duration": 65}}},
        )
        text = chain_text(L.build_dynamic_chain(it))
        assert "播放 12" in text
        assert "时长 1:05" in text
        assert "弹幕" not in text

    def test_forward_without_orig_text(self):
        it = item(id_str="6", type_="DYNAMIC_TYPE_FORWARD", dynamic={"major": {}})
        text = chain_text(L.build_dynamic_chain(it))
        assert "转发动态" in text

    def test_text_truncated(self):
        it = item(id_str="7", desc="字" * 500)
        text = chain_text(L.build_dynamic_chain(it))
        body = text.splitlines()[1]
        assert body.endswith("...")
        assert len(body) == 300 + 3


class TestBuildLiveChain:
    def test_live_with_cover_and_room(self):
        chain = L.build_live_chain(
            {"uname": "UP", "title": "标题", "room_id": 123, "cover": "https://c/1.jpg"},
            now_live=True,
        )
        text = chain_text(chain)
        assert text.startswith("[B站直播] UP 开播了\n标题")
        assert count_images(chain) == 1
        assert "https://live.bilibili.com/123" in text

    def test_live_without_room_or_cover(self):
        chain = L.build_live_chain({"uname": "", "title": ""}, now_live=True)
        assert chain_text(chain) == "[B站直播] UP主 开播了"
        assert count_images(chain) == 0

    def test_offline(self):
        chain = L.build_live_chain({"uname": "UP", "room_id": 1, "cover": "https://c/1.jpg"}, now_live=False)
        assert chain_text(chain) == "[B站直播] UP 下播了"
        assert count_images(chain) == 0


# ───────────────────────── 过滤 ─────────────────────────


class TestShouldFilterDynamic:
    def test_no_filter(self):
        assert not L.should_filter_dynamic(item(), Subscription(uid=1))

    def test_forward_filters(self):
        forward = item(id_str="1", type_="DYNAMIC_TYPE_FORWARD")
        assert L.should_filter_dynamic(forward, Subscription(uid=1, filter_types=["forward"]))
        assert not L.should_filter_dynamic(forward, Subscription(uid=1, filter_types=["forward_lottery"]))
        assert not L.should_filter_dynamic(forward, Subscription(uid=1, filter_types=["lottery"]))
        assert not L.should_filter_dynamic(forward, Subscription(uid=1, filter_types=["video"]))

    def test_forward_lottery_filter(self):
        lottery = {
            "id_str": "1",
            "type": "DYNAMIC_TYPE_FORWARD",
            "orig": {"modules": {"module_dynamic": {"type": "DYNAMIC_TYPE_LOTTERY"}}},
        }
        assert L.should_filter_dynamic(lottery, Subscription(uid=1, filter_types=["forward_lottery"]))
        assert not L.should_filter_dynamic(lottery, Subscription(uid=1, filter_types=["video"]))

    def test_lottery_text_filter(self):
        draw = item(id_str="1", type_="DYNAMIC_TYPE_DRAW", desc="互动抽奖开始啦")
        assert L.should_filter_dynamic(draw, Subscription(uid=1, filter_types=["lottery"]))
        forward = item(id_str="2", type_="DYNAMIC_TYPE_FORWARD", desc="互动抽奖开始啦")
        assert L.should_filter_dynamic(forward, Subscription(uid=1, filter_types=["lottery"]))
        assert L.should_filter_dynamic(draw, Subscription(uid=1, filter_types=["draw"]))
        assert not L.should_filter_dynamic(draw, Subscription(uid=1, filter_types=["video"]))

    def test_word_and_draw(self):
        word = item(id_str="1", type_="DYNAMIC_TYPE_WORD", desc="普通文字")
        assert L.should_filter_dynamic(word, Subscription(uid=1, filter_types=["draw"]))
        assert not L.should_filter_dynamic(word, Subscription(uid=1, filter_types=["lottery"]))

    def test_av_and_article_filters(self):
        av = item(id_str="1", type_="DYNAMIC_TYPE_AV")
        article = item(id_str="2", type_="DYNAMIC_TYPE_ARTICLE")
        assert L.should_filter_dynamic(av, Subscription(uid=1, filter_types=["video"]))
        assert not L.should_filter_dynamic(av, Subscription(uid=1, filter_types=["article"]))
        assert L.should_filter_dynamic(article, Subscription(uid=1, filter_types=["article"]))
        assert not L.should_filter_dynamic(article, Subscription(uid=1, filter_types=["video"]))

    def test_regex_filters(self):
        it = item(id_str="1", desc="限时抽奖活动")
        assert L.should_filter_dynamic(it, Subscription(uid=1, filter_regex=["抽奖"]))
        assert not L.should_filter_dynamic(it, Subscription(uid=1, filter_regex=["^视频$"]))
        # 无效正则不应抛异常
        assert not L.should_filter_dynamic(it, Subscription(uid=1, filter_regex=["["]))

    def test_regex_not_applied_without_text(self):
        it = {"id_str": "1", "type": "DYNAMIC_TYPE_AV", "modules": {}}
        assert not L.should_filter_dynamic(it, Subscription(uid=1, filter_regex=["x"]))


# ───────────────────────── 订阅辅助 ─────────────────────────


class TestAppendRecentId:
    def test_append_and_trim(self, make_sub):
        sub = make_sub()
        for i in range(1, 6):
            L.append_recent_id(sub, str(i), 3)
        assert sub.recent_ids == ["3", "4", "5"]

    def test_duplicate_moves_to_end(self):
        sub = Subscription(uid=1, recent_ids=["1", "2", "3"])
        L.append_recent_id(sub, "1", 5)
        assert sub.recent_ids == ["2", "3", "1"]

    def test_zero_limit_and_empty_id(self):
        sub = Subscription(uid=1, recent_ids=["1"])
        L.append_recent_id(sub, "2", 0)
        assert sub.recent_ids == []
        sub2 = Subscription(uid=1)
        L.append_recent_id(sub2, "", 5)
        assert sub2.recent_ids == []


class TestBuildUidTargets:
    def test_merge_sessions(self, make_sub):
        targets = L.build_uid_targets(
            {
                "s1": [make_sub(2), make_sub(3)],
                "s2": [make_sub(2)],
            }
        )
        assert set(targets) == {2, 3}
        assert len(targets[2]) == 2
        assert targets[2][0][0] == "s1"

    def test_empty(self):
        assert L.build_uid_targets({}) == {}


class TestLatestDynamicId:
    def test_skips_top(self):
        items = [
            item(id_str="top", tag="置顶"),
            item(id_str="new"),
            item(id_str="old"),
        ]
        assert L.latest_dynamic_id(items) == "new"

    def test_all_top_or_invalid(self):
        assert L.latest_dynamic_id([item(id_str="a", tag="置顶")]) == ""
        assert L.latest_dynamic_id([{"id_str": ""}]) == ""
        assert L.latest_dynamic_id(["bad", None]) == ""
        assert L.latest_dynamic_id([]) == ""


class TestCollectNewDynamics:
    def test_stops_at_known(self):
        items = [item(id_str="1"), item(id_str="2"), item(id_str="3")]
        assert [H.dynamic_id(i) for i in L.collect_new_dynamics(items, {"3"})] == ["1", "2"]

    def test_skips_pinned_but_continues(self):
        items = [item(id_str="top", tag="置顶"), item(id_str="1"), item(id_str="2")]
        assert [H.dynamic_id(i) for i in L.collect_new_dynamics(items, {"2"})] == ["1"]

    def test_breaks_on_missing_id_and_ignores_non_dict(self):
        items = ["bad", item(id_str="1"), {"id_str": ""}, item(id_str="3")]
        assert [H.dynamic_id(i) for i in L.collect_new_dynamics(items, set())] == ["1"]

    def test_empty(self):
        assert L.collect_new_dynamics([], set()) == []


class TestPickPushBatch:
    def test_takes_newest_and_returns_chronological(self):
        # 新 → 旧
        newest_first = [item(id_str="3"), item(id_str="2"), item(id_str="1")]
        batch = L.pick_push_batch(newest_first, 2)
        # 取最新的两条（3、2），并按时间正序推送
        assert [H.dynamic_id(i) for i in batch] == ["2", "3"]
        assert H.dynamic_id(batch[-1]) == "3"

    def test_limit_larger_than_items(self):
        newest_first = [item(id_str="2"), item(id_str="1")]
        assert [H.dynamic_id(i) for i in L.pick_push_batch(newest_first, 10)] == ["1", "2"]

    def test_empty_and_non_positive_limit(self):
        assert L.pick_push_batch([], 3) == []
        assert L.pick_push_batch([item(id_str="1")], 0) == []


class TestLatestDynamicRegression:
    """回归用例：锚点必须跳过置顶，推送必须命中最新动态。"""

    def test_real_feed_anchor_and_push(self):
        items = normalized("dynamic_feed_jiaran.json")
        assert H.is_top_dynamic(items[0])

        # 1) 订阅时的锚点 = 最新非置顶动态
        anchor = L.latest_dynamic_id(items)
        newest_id = H.dynamic_id(items[1])
        assert anchor == newest_id

        # 2) 已读全部 → 不应产生任何新动态
        assert L.collect_new_dynamics(items, {anchor}) == []

        # 3) 模拟"订阅时错误地把置顶当作锚点"（旧 bug）：
        #    必须把最新动态识别出来，而不是把更旧的动态标记为已读
        wrong_anchor = H.dynamic_id(items[0])
        new_items = L.collect_new_dynamics(items, {wrong_anchor})
        batch = L.pick_push_batch(new_items, 3)
        assert batch, "存在未读动态"
        assert H.dynamic_id(batch[-1]) == newest_id, "推送批次必须包含最新动态"

    def test_feed_with_only_pinned_stays_empty(self):
        items = [item(id_str="top", tag="置顶")]
        assert L.latest_dynamic_id(items) == ""
        assert L.pick_push_batch(L.collect_new_dynamics(items, set()), 3) == []
