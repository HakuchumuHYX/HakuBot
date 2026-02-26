from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..types import PickStrategy


@dataclass(slots=True)
class Plan:
    """
    点歌"规划结果"：
    - search_query: 用于直接搜歌的 query（尽量短）
    - need_web_search: 是否需要走联网慢路径
    - pick_strategy: random/first/best_match
    - platform_hint: 目前实现里已固定网易云，因此应始终为 None（保留字段做兼容）
    - song_title/song_artist: LLM 抽取的结构化信息
    - web_queries: need_web_search 为 True 时，给 Tavily 的 query 列表
    - context_style/domain_hint: 语境识别与站点倾向
    - alternative_queries: 备选搜索词列表（原名/音译/变体）
    - confidence: LLM对解析结果的置信度(0-100)
    - parse_reason: LLM解析的理由说明（便于调试）
    """

    search_query: str
    need_web_search: bool = False
    pick_strategy: PickStrategy = "random"
    platform_hint: Optional[str] = None

    song_title: Optional[str] = None
    song_artist: Optional[str] = None

    web_queries: Optional[list[str]] = None

    context_style: Optional[str] = None  # meme|anime|game|music|general
    domain_hint: Optional[str] = None  # bilibili|zhihu|moegirl|wiki|null

    # 新增优化字段
    alternative_queries: Optional[list[str]] = None  # 备选搜索词（原名/音译/变体）
    confidence: int = 50  # 解析置信度(0-100)，默认50表示中等确定
    parse_reason: Optional[str] = None  # 解析理由说明
