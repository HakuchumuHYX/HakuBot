import json
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel


class MusicConfig(BaseModel):
    # === 原 music_plugin 配置（已迁移到 ai_assistant 下）===
    default_player_name: str = "网易云音乐"
    nodejs_base_url: str = "https://163api.qijieya.cn"

    song_limit: int = 5
    select_mode: str = "text"
    send_modes: list[str] = ["card", "record", "file", "text"]

    enable_comments: bool = True
    enable_lyrics: bool = False

    proxy: Optional[str] = None
    timeout: int = 30
    timeout_recall: bool = True
    clear_cache: bool = True
    playlist_limit: int = 100

    # 网易云热评参数（来源：原 astrbot_plugin_music 默认值）
    enc_sec_key: str = ""
    enc_params: str = ""

    # === AI 点歌行为配置 ===
    allow_web_search: bool = True
    candidate_limit: int = 5
    pick_default: str = "random"  # random/first/best_match
    fast_path_hint: str = "好哦，正在为你寻找……"
    slow_path_hint: str = "别急，再等等哦，我去查查相关资料～"

    # 解析意图时给 LLM 的参数（尽量低随机，快且稳定）
    llm_temperature: Optional[float] = 0.2
    llm_top_p: Optional[float] = None

    # 慢路径 Tavily 搜索质量不佳时，允许重试次数（默认 1：最多再搜一次）
    web_search_retry_times: int = 1
    # 启用“站点倾向/白名单”
    web_search_domain_bias_enabled: bool = True

    # === debug 日志 ===
    debug_log: bool = False
    debug_log_max_chars: int = 1200
    debug_log_include_web_content: bool = False


class ChatConfig(BaseModel):
    model: str = "gpt-3.5-turbo"
    max_tokens: Optional[int] = 8192
    system_prompt: str = (
        "你好！我是HakuBot的AI助手。请用活泼、亲切且自然的语气回答用户的问题。"
        "避免过于生硬的机器回复。如果回答包含长文本，请注意分段和排版。"
    )
    watermark: str = ""
    # 图片回复的背景颜色，默认为浅灰色（护眼白），例如也可用绿豆沙色 #C7EDCC 等
    bg_color: str = "#f8f9fa"


class ImageConfig(BaseModel):
    model: str = "dall-e-3"
    size: Optional[str] = None
    quality: Optional[str] = None # standard, hd, medium
    size_param: Optional[str] = None # 1K, 2K, 4K

    # 当中转商的生图模型仅提供 /v1/chat/completions 端点时，设为 True
    # False（默认）：走标准 /images/generations + /images/edits
    # True：走 /chat/completions 兼容路径
    use_chat_endpoint: bool = False
    
    # --- Image generation reliability / safety fallback ---
    # 当生图返回 content=None / 无 images 时，自动进行“安全改写”并重试，以提升成功率
    retry_on_empty: bool = True
    # 最大重试次数（建议 1；过多会增加成本与延迟）
    retry_max_times: int = 1
    # 安全改写使用的模型（默认 None 表示复用 chat.model）
    safe_rewrite_model: Optional[str] = None
    # 安全改写最大 token
    safe_rewrite_max_tokens: int = 256


class SearchConfig(BaseModel):
    # --- Web Search (manual command only) ---
    # Tavily: https://tavily.com/
    tavily_api_key: Optional[str] = None
    max_results: int = 5
    depth: str = "basic"

    # --- Web Search Query Rewrite / Multi-query ---
    # 启用后，会先对用户输入做“检索 query 提炼/重写”，再进行搜索，避免直接拿长段口语去搜。
    query_rewrite: bool = True
    # 允许使用 LLM 进行 query 重写（默认启用；会产生一次额外模型调用，仅在触发条件满足时执行）
    query_rewrite_use_llm: bool = True
    # 当原始文本长度超过该阈值时，触发一次 LLM 重写（否则只用启发式提炼）
    query_rewrite_llm_trigger_len: int = 200
    # 最终单条 query 最大长度（字符数）
    query_max_len: int = 120
    # 一次问题最多生成多少条 query（多角度检索）
    num_queries: int = 3


class PluginConfig(BaseModel):
    # --- Provider Switch ---
    # openai_compatible: 走 /chat/completions 的 OpenAI 兼容接口（保持现状）
    # google_ai_studio: 走 Google AI Studio / Gemini Developer API 官方接口（generateContent）
    provider: str = "openai_compatible"

    # OpenAI compatible config (legacy / default)
    api_key: str
    base_url: str = "https://api.openai.com/v1"

    # Google AI Studio config (optional)
    # 为空时会回退使用 api_key
    google_api_key: Optional[str] = None
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    proxy: Optional[str] = None
    timeout: float = 60.0

    # Modules
    chat: ChatConfig = ChatConfig()
    image: ImageConfig = ImageConfig()
    search: SearchConfig = SearchConfig()
    music: MusicConfig = MusicConfig()


CURRENT_PATH = Path(__file__).parent
CONFIG_PATH = CURRENT_PATH / "config.json"


def load_config() -> PluginConfig:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件未找到: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Migrate old flat config to nested config structure if needed
    if "chat_model" in data or "image_model" in data:
        migrated = {
            "provider": data.get("provider", "openai_compatible"),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", "https://api.openai.com/v1"),
            "google_api_key": data.get("google_api_key"),
            "google_base_url": data.get("google_base_url", "https://generativelanguage.googleapis.com/v1beta"),
            "proxy": data.get("proxy"),
            "timeout": data.get("timeout", 60.0),
            "chat": {
                "model": data.get("chat_model", "gpt-3.5-turbo"),
                "max_tokens": data.get("chat_max_tokens", 8192),
                "system_prompt": data.get("system_prompt", "")
            },
            "image": {
                "model": data.get("image_model", "dall-e-3"),
                "size": data.get("image_size"),
                "quality": data.get("image_quality"),
                "size_param": data.get("image_size_param"),
                "retry_on_empty": data.get("image_retry_on_empty", True),
                "retry_max_times": data.get("image_retry_max_times", 1),
                "safe_rewrite_model": data.get("image_safe_rewrite_model"),
                "safe_rewrite_max_tokens": data.get("image_safe_rewrite_max_tokens", 256)
            },
            "search": {
                "tavily_api_key": data.get("tavily_api_key"),
                "max_results": data.get("web_search_max_results", 5),
                "depth": data.get("web_search_depth", "basic"),
                "query_rewrite": data.get("web_search_query_rewrite", True),
                "query_rewrite_use_llm": data.get("web_search_query_rewrite_use_llm", True),
                "query_rewrite_llm_trigger_len": data.get("web_search_query_rewrite_llm_trigger_len", 200),
                "query_max_len": data.get("web_search_query_max_len", 120),
                "num_queries": data.get("web_search_num_queries", 3)
            },
            "music": data.get("music", {})
        }
        data = migrated
        # Auto-save migrated config
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    return PluginConfig(**data)


def save_config(config: PluginConfig):
    """保存配置到文件"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        # 使用 dict() 以兼容 Pydantic V1/V2，确保中文不乱码
        json.dump(config.dict(), f, indent=4, ensure_ascii=False)


plugin_config = load_config()
