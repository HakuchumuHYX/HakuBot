"""HLTV 订阅插件配置"""

from pydantic import BaseModel
from nonebot import get_plugin_config


class Config(BaseModel):
    """HLTV 订阅插件配置"""
    
    # HLTV API 配置
    hltv_max_delay: float = 30.0  # 最大延迟（秒）- 增加以应对 CF 保护
    hltv_min_delay: float = 5.0   # 最小延迟（秒）- 增加以减少触发 CF
    hltv_max_retries: int = 10    # 最大重试次数 - 增加以提高成功率
    hltv_timeout: int = 15        # 超时时间（秒）
    hltv_timezone: str = "Asia/Shanghai"  # 时区
    
    # 代理配置（可选）
    hltv_proxy_list: list[str] = []  # 代理列表
    hltv_proxy_path: str = ""         # 代理文件路径
    
    # 功能配置
    hltv_enabled_groups: list[int] = []  # 启用的群聊列表（空列表表示所有群聊都启用）


# 获取配置
plugin_config = get_plugin_config(Config)
