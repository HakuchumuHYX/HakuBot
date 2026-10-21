from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths
from typing import Optional, List
from pydantic import Field


class LLMConfig(ConfigModel):
    # openai_compatible: /chat/completions (OpenAI 兼容接口，保持现状)
    provider: str

    # OpenAI compatible config
    api_key: str
    base_url: str

    model: str
    max_tokens: int
    thinking_enabled: bool
    reasoning_effort: Optional[str]
    extra_body: dict
    timeout: float
    proxy: Optional[str]


class PluginConfig(ConfigModel):
    llm: LLMConfig
    max_concurrent_tasks: int = Field(ge=1)
    max_messages: int  # 设为 0 表示不限制最大消息数，依靠 LLM 长窗口和自适应算法处理
    analysis_days: int
    auto_analysis_time: str
    enable_auto_analysis: bool
    bot_qq_ids: List[str]
    enable_user_card: bool
    report_template: str
    min_messages_threshold: int
    watermark_text: str

    # Analysis toggles
    topic_analysis_enabled: bool
    user_title_analysis_enabled: bool
    golden_quote_analysis_enabled: bool

    # Analysis limits
    max_topics: int
    max_user_titles: int
    max_golden_quotes: int

    # Map-Reduce Settings
    max_input_length: int  # 字符数阈值，超过则分块 (建议调大以发挥现代长窗口模型优势)

    # Prompts
    topic_analysis_prompt: str
    topic_merge_prompt: str

    user_title_analysis_prompt: str

    golden_quote_analysis_prompt: str
    golden_quote_merge_prompt: str


CONFIG_PATH = PluginPaths("group_daily_analysis").config / "config.json"


plugin_config = load_config("group_daily_analysis", PluginConfig)
if plugin_config.llm.provider != "openai_compatible":
    raise ValueError(f"Unsupported LLM provider: {plugin_config.llm.provider}")
