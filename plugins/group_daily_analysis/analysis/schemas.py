from pydantic import BaseModel, Field


class SummaryTopicItem(BaseModel):
    topic: str
    contributors: list[str] = Field(default_factory=list)
    detail: str


class UserTitleItem(BaseModel):
    name: str
    qq: int | None = None
    title: str
    reason: str
    personality: str = ""


class GoldenQuoteItem(BaseModel):
    content: str
    sender: str
    reason: str
    qq: int | None = None


class TopicsPayload(BaseModel):
    items: list[SummaryTopicItem] = Field(default_factory=list)


class UserTitlesPayload(BaseModel):
    items: list[UserTitleItem] = Field(default_factory=list)


class GoldenQuotesPayload(BaseModel):
    items: list[GoldenQuoteItem] = Field(default_factory=list)


class TopicsAndQuotesPayload(BaseModel):
    topics: list[SummaryTopicItem] = Field(default_factory=list)
    quotes: list[GoldenQuoteItem] = Field(default_factory=list)
