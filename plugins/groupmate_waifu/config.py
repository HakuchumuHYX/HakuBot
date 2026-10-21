"""
groupmate_waifu/config.py
插件配置类定义
"""

from core.settings import ConfigModel


class Config(ConfigModel):
    """娶群友插件配置"""

    # 是否保存数据到文件
    waifu_save: bool

    # 是否每日重置记录（True: 每日重置所有记录, False: 只重置单身和涩涩记录）
    waifu_reset: bool

    # 娶群友 HE（Happy Ending）概率阈值（1-100）
    waifu_he: int

    # 娶群友 BE（Bad Ending）概率范围（HE 到 HE+BE 之间为 BE）
    waifu_be: int

    # NTR 成功概率阈值（1-100）
    waifu_ntr: int

    # 透群友 HE 概率阈值（1-100）
    yinpa_he: int

    # 透群友 BE 概率范围
    yinpa_be: int

    # 透 CP 成功概率阈值（0 表示使用 yinpa_he）
    yinpa_cp: int

    # 群成员最后发言时间过滤器（秒），默认 7 天
    waifu_last_sent_time_filter: int
