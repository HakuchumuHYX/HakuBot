from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class PixivPage:
    index: int
    url: str
    original_url: str
    ext: str


@dataclass(frozen=True)
class PixivIllust:
    pid: int
    title: str
    author: str
    author_id: int
    x_restrict: int
    page_count: int
    pages: List[PixivPage]
    web_url: str
    illust_type: str = "illust"

    @property
    def is_r18(self) -> bool:
        return self.x_restrict == 1

    @property
    def is_r18g(self) -> bool:
        return self.x_restrict == 2

    @property
    def is_restricted(self) -> bool:
        return self.x_restrict in {1, 2}

    @property
    def is_ugoira(self) -> bool:
        return self.illust_type == "ugoira"


@dataclass(frozen=True)
class UgoiraFrame:
    file: str
    delay: int


@dataclass(frozen=True)
class UgoiraMetadata:
    zip_url: str
    frames: List[UgoiraFrame]
