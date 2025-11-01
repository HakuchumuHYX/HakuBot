from pydantic import BaseModel, Field
from nonebot import get_plugin_config
from typing import Set

class Config(BaseModel):
    """Plugin Config Here"""

    plus_one_priority: int = (Field(1, doc="plus_one 响应优先级"))
    plus_one_black_list: list = (Field([], doc="plus_one 黑名单"))
    blocked_words: set[str] = {'jrrp', '绑定', '个人信息', '抽签', '娶群友', 'pjsk', 'cn', 'tw', 'en', 'jp', '鉴定', '签到', '练歌', 'gs', '猜歌'}


config = get_plugin_config(Config)
