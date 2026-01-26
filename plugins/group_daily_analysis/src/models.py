from typing import List, Dict, Optional
from pydantic import BaseModel

class SummaryTopic(BaseModel):
    topic: str
    contributors: List[str]
    detail: str

class UserTitle(BaseModel):
    name: str
    qq: Optional[int]
    title: str
    mbti: str
    reason: str

class GoldenQuote(BaseModel):
    content: str
    sender: str
    reason: str
    qq: Optional[int] = None

class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ActivityVisualization(BaseModel):
    hourly_activity: Dict[int, int]  # hour -> count

class EmojiStatistics(BaseModel):
    total_emoji_count: int = 0
    face_count: int = 0
    mface_count: int = 0
    bface_count: int = 0
    sface_count: int = 0
    other_emoji_count: int = 0
    face_details: Dict[str, int] = {}

class GroupStatistics(BaseModel):
    message_count: int
    total_characters: int
    participant_count: int
    most_active_period: str
    emoji_count: int
    emoji_statistics: EmojiStatistics
    activity_visualization: ActivityVisualization
    golden_quotes: List[GoldenQuote] = []
    token_usage: TokenUsage = TokenUsage()

class AnalysisResult(BaseModel):
    statistics: GroupStatistics
    topics: List[SummaryTopic]
    user_titles: List[UserTitle]
