from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths


class LLMConfig(ConfigModel):
    api_key: str
    base_url: str
    model: str
    max_tokens: int
    thinking_enabled: bool
    reasoning_effort: str | None
    extra_body: dict
    timeout: float
    proxy: str | None


class PluginConfig(ConfigModel):
    llm: LLMConfig
    analysis_days: int
    auto_analysis_time: str
    enable_auto_analysis: bool
    bot_qq_ids: list[str]
    enable_user_card: bool
    min_messages_threshold: int
    watermark_text: str

    max_topics: int
    max_user_titles: int
    max_golden_quotes: int
    # 转写超过这么多字就丢掉最早的消息；实测约 0.7~0.8 token/字
    max_input_length: int

    topic_analysis_prompt: str
    user_title_analysis_prompt: str
    golden_quote_analysis_prompt: str


plugin_config = load_config("group_daily_analysis", PluginConfig)
DATA_DIR = PluginPaths("group_daily_analysis").data
