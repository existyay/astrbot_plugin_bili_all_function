from dataclasses import dataclass, field
from typing import List


@dataclass
class Subscription:
    uid: int
    last_dynamic_id: str = ""
    is_live: bool = False
    filter_types: List[str] = field(default_factory=list)
    filter_regex: List[str] = field(default_factory=list)
    recent_ids: List[str] = field(default_factory=list)
