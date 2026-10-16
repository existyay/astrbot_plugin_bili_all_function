"""数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Subscription:
    uid: int
    last_dynamic_id: str = ""
    is_live: bool = False
    filter_types: list[str] = field(default_factory=list)
    filter_regex: list[str] = field(default_factory=list)
    recent_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GroupBinding:
    """设置面板里声明的「通知群 → UP 主」订阅映射（一行一条）。"""

    session: str
    uid: int
    filter_types: list[str] = field(default_factory=list)
    filter_regex: list[str] = field(default_factory=list)

    def same_filters(self, other: Subscription) -> bool:
        """过滤器是否与既有订阅一致（用于判断是否需要更新）。"""
        return self.filter_types == list(other.filter_types) and self.filter_regex == list(other.filter_regex)
