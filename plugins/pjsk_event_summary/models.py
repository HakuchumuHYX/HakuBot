from pydantic import BaseModel, Field


class Character(BaseModel):
    name: str
    color: str
    on_color: str


class EventMeta(BaseModel):
    """masterdata 推导出的展示信息，列表和详情共用"""

    event_id: int
    unit: str
    unit_color: str
    chars: list[Character]
    color: str
    on_color: str
    type: str
    start: str
    end: str
    logo: str
    character: str  # 活动立绘，World Link 为空


class Chapter(BaseModel):
    chapter_no: int
    title_jp: str
    title_cn: str
    summary_cn: str
    image_url: str = ""  # 由资源站推导，不用 MoeSekai JSON 自带的旧图床地址


class EventDetail(EventMeta):
    title_jp: str
    title_cn: str
    outline_jp: str | None = ""
    outline_cn: str | None = ""
    summary_cn: str | None = ""
    chapters: list[Chapter] = Field(default_factory=list)


class EventSimple(EventMeta):
    title_jp: str
    title_cn: str
    chapter_count: int
