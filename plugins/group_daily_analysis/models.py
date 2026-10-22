from dataclasses import dataclass, field

from pydantic import BaseModel

from utils.llm.client import TokenUsage


# 以下三个也用来校验 LLM 输出
class SummaryTopic(BaseModel):
    topic: str
    contributors: list[str] = []
    detail: str


class UserTitle(BaseModel):
    name: str
    qq: int | None = None
    title: str
    reason: str
    personality: str = ""


class GoldenQuote(BaseModel):
    content: str
    sender: str
    reason: str
    qq: int | None = None


@dataclass
class Statistics:
    message_count: int = 0
    total_characters: int = 0
    participant_count: int = 0
    emoji_count: int = 0
    hourly: list[int] = field(default_factory=lambda: [0] * 24)

    @property
    def most_active_period(self) -> str:
        peak = self.hourly.index(max(self.hourly))
        return f"{peak:02d}:00-{(peak + 1) % 24:02d}:00"


@dataclass
class AnalysisResult:
    statistics: Statistics
    topics: list[SummaryTopic] = field(default_factory=list)
    user_titles: list[UserTitle] = field(default_factory=list)
    golden_quotes: list[GoldenQuote] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
