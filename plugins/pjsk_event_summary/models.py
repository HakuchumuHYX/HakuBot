from pydantic import BaseModel
from typing import List, Optional

class Chapter(BaseModel):
    chapter_no: int
    title_jp: str
    title_cn: str
    summary_cn: str
    image_url: str

class EventDetail(BaseModel):
    event_id: int
    title_jp: str
    title_cn: str
    outline_jp: Optional[str] = ""
    outline_cn: Optional[str] = ""
    summary_cn: Optional[str] = ""
    cover_image_url: Optional[str] = ""
    chapters: List[Chapter] = []

class EventSimple(BaseModel):
    event_id: int
    title_jp: str
    title_cn: str
    chapter_count: int
    summary_status: str
    cover_image_url: Optional[str] = ""