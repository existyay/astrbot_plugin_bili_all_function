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
