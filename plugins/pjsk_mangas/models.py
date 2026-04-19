from __future__ import annotations

from pydantic import BaseModel, Field


class MangaSimple(BaseModel):
    id: int
    title: str
    published_at: int | None = None


class MangaDetail(BaseModel):
    id: int
    title: str
    post_url: str = ""
    image_url: str = ""
    relative_path: str = ""
    published_at: int | None = None
    contributors: dict[str, str] = Field(default_factory=dict)
