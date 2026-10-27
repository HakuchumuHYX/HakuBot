from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from utils.llm.client import TokenUsage


# 以下三个也用来校验 LLM 输出
class SummaryTopic(BaseModel):
    topic: str
    contributors: list[str] = []
    detail: str
    # 话题第一条消息的 HH:MM，LLM 照抄转写；at 是按它在转写里找到的那一行的完整时间
    time: str = ""
    at: datetime | None = None


class UserTitle(BaseModel):
    name: str
    qq: int | None = None
    title: str
    reason: str
    personality: str = ""
    # 转写里的发言条数，不问 LLM
    message_count: int = 0


class GoldenQuote(BaseModel):
    content: str
    sender: str
    reason: str
    qq: int | None = None
    # 按原文在转写里查到的那一行的时间，不问 LLM
    at: datetime | None = None


@dataclass
class Statistics:
    message_count: int = 0
    total_characters: int = 0
    participant_count: int = 0
    emoji_count: int = 0
    hourly: list[int] = field(default_factory=lambda: [0] * 24)


@dataclass
class AnalysisResult:
    statistics: Statistics
    headline: str = ""
    topics: list[SummaryTopic] = field(default_factory=list)
    user_titles: list[UserTitle] = field(default_factory=list)
    golden_quotes: list[GoldenQuote] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
