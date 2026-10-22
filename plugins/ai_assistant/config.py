from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths
from typing import Optional

from pydantic import Field


class ChatConfig(ConfigModel):
    model: str
    max_tokens: Optional[int]
    # 采样温度，控制回复的随机性/创造性（0.0~2.0）
    temperature: Optional[float]
    # 上游私有参数原样透传，例如 {"thinking": {"type": "enabled"}}
    extra_body: dict
    system_prompt: str
    # 底线规则：放在所有 system 消息之后，优先级高于人设和用户要求；留空则不注入
    bottom_line: str
    watermark: str
    # 图片回复的背景颜色，例如浅灰 #f8f9fa、绿豆沙色 #C7EDCC
    bg_color: str
    # 回复链历史的有效期，从回答发出时算起
    history_ttl_minutes: int = Field(ge=1)


class ImageConfig(ConfigModel):
    model: str
    size: Optional[str]
    quality: Optional[str]  # standard, hd, medium
    # Images API 没有 system 角色；该文本会作为中性默认指令放在用户需求之前
    prompt_prefix: str
    # 底线规则：追加在 prompt 最末尾，优先于用户需求和联网参考；留空则不追加
    bottom_line: str
    # 生图单独的读超时；上游 generations 超过约 120s 会被 Cloudflare 以 524 断开
    timeout: float


class PluginConfig(ConfigModel):
    # Chat 与生图共用的 OpenAI-compatible 连接配置
    api_key: str
    base_url: str
    proxy: Optional[str]
    timeout: float
    # Tavily: https://tavily.com/
    tavily_api_key: str

    chat: ChatConfig
    image: ImageConfig


CONFIG_PATH = PluginPaths("ai_assistant").config / "config.json"

plugin_config = load_config("ai_assistant", PluginConfig)
