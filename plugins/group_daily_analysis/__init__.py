"""
每日群聊总结
记录开了日报的群的消息，每天定时（或超管手动）让 LLM 写话题、群友称号、金句，渲染成长图发群。
"""

from plugins.group_daily_analysis import handlers, recorder  # noqa: F401
