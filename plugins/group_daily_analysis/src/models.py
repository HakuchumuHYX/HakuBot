from typing import List, Dict, Optional, Any
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
    daily_activity: Dict[str, int] = {}  # date -> count (YYYY-MM-DD)
    user_activity_ranking: List[Dict[str, Any]] = []  # 用户活跃度排行
    peak_hours: List[int] = []  # 高峰时段列表（小时）

class EmojiStatistics(BaseModel):
    face_count: int = 0  # QQ基础表情数量
    mface_count: int = 0  # 动画表情数量
    bface_count: int = 0  # 超级表情数量
    sface_count: int = 0  # 小表情数量
    other_emoji_count: int = 0  # 其他表情数量
    face_details: Dict[str, int] = {}  # 具体表情ID统计
    
    @property
    def total_emoji_count(self) -> int:
        """总表情数量"""
        return (
            self.face_count
            + self.mface_count
            + self.bface_count
            + self.sface_count
            + self.other_emoji_count
        )

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
    golden_quotes: List[GoldenQuote] = []
