from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths
from typing import Any, Optional

from pydantic import Field


class ChatConfig(ConfigModel):
    # --- Per-module provider override (留空则回退到全局配置) ---
    provider: Optional[str]
    api_key: Optional[str]
    base_url: Optional[str]

    model: str
    max_tokens: Optional[int]
    thinking_enabled: bool
    reasoning_effort: Optional[str]
    extra_body: dict
    system_prompt: str
    # 底线规则：放在所有 system 消息之后、用户消息之前，优先级高于人设和用户要求；留空则不注入
    bottom_line: str
    # 采样温度，控制回复的随机性/创造性（0.0~2.0，Claude 建议 0.7 左右）
    temperature: Optional[float]
    # nucleus sampling，与 temperature 二选一微调即可
    top_p: Optional[float]
    # 图片最大边长（像素），超过此值会等比缩放+JPEG压缩，以减少视觉API的token消耗
    # 设为 0 或 None 则不压缩
    image_max_size: int
    # 回复合并转发时，最多展开的节点数量
    forward_max_nodes: int = Field(ge=1)
    # 回复合并转发时，最多传入模型的图片数量
    forward_max_images: int = Field(ge=0)
    # 回复合并转发时，文本上下文最大字符数
    forward_max_text_chars: int = Field(ge=500)
    # 是否把合并转发内图片作为多模态输入传给模型
    forward_include_images: bool
    watermark: str
    # 图片回复的背景颜色，默认为浅灰色（护眼白），例如也可用绿豆沙色 #C7EDCC 等
    bg_color: str
    # 运行时上下文使用的时区，用于纠正模型对当前日期的误判
    runtime_timezone: str


class ImageConfig(ConfigModel):
    model: str
    size: Optional[str]
    quality: Optional[str]  # standard, hd, medium
    # Images API 没有 system 角色；该文本会作为中性默认指令放在用户需求之前
    prompt_prefix: str
    # 底线规则：追加在 prompt 最末尾，优先于用户需求和联网参考；留空则不追加
    bottom_line: str
    # 图生图参考图只在超过此边长时缩放，并保留 PNG/WebP/JPEG 格式和透明通道
    reference_image_max_size: int

    # 生图可单独使用更长的读超时；None 时回退到全局 timeout
    timeout: Optional[float]
    # OpenAI SDK 传输层重试次数；建议设为 0，避免重复计费/重复任务
    api_max_retries: int


class SearchConfig(ConfigModel):
    # --- Tavily ---
    # Tavily: https://tavily.com/
    tavily_api_key: Optional[str]

    # --- Web Search Query Rewrite / Multi-query ---
    # 启用后，会先对用户输入做“检索 query 提炼/重写”，再进行搜索，避免直接拿长段口语去搜。
    query_rewrite: bool
    # 允许使用 LLM 进行 query 重写（默认启用；会产生一次额外模型调用，仅在触发条件满足时执行）
    query_rewrite_use_llm: bool
    # 当原始文本长度超过该阈值时，触发一次 LLM 重写（否则只用启发式提炼）
    query_rewrite_llm_trigger_len: int
    # 最终单条 query 最大长度（字符数）
    query_max_len: int
    # 一次问题最多生成多少条 query（多角度检索）
    num_queries: int

    # --- Auto Search Harness ---
    # 普通 /chat 是否根据问题内容自动轻量联网
    auto_search_enabled: bool
    # off: 不自动联网；smart: 仅命中新鲜度信号时联网；always: 每次 /chat 都轻量联网
    auto_search_mode: str
    # 自动 quick 搜索的最大结果数，默认小于手动 /chat联网
    auto_search_quick_max_results: int
    # 自动 deep 或强制联网的最大结果数
    auto_search_deep_max_results: int
    # 自动 quick 搜索注入模型的证据包最大字符数
    auto_search_context_max_chars: int

    # --- Chat Web Search ---
    chat_max_results: int
    chat_depth: str
    chat_chunks_per_source: int
    chat_include_answer: Any
    chat_include_raw_content: Any
    chat_auto_parameters: bool
    chat_content_max_chars: int
    chat_raw_content_max_chars: int
    chat_context_max_chars: int

    # --- Image Web Search ---
    image_max_results: int
    image_depth: str
    image_chunks_per_source: int
    image_include_answer: Any
    image_include_raw_content: Any
    image_include_images: bool
    image_include_image_descriptions: bool
    image_auto_parameters: bool
    image_visual_brief_model: Optional[str]
    image_visual_brief_max_tokens: int
    image_raw_content_max_chars: int
    image_content_max_chars: int
    image_max_reference_images: int


class ResolvedProviderConfig:
    """resolve() 返回的连接参数集合，供 service 层直接使用。"""

    __slots__ = ("provider", "api_key", "base_url")

    def __init__(self, provider: str, api_key: str, base_url: str):
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url


class PluginConfig(ConfigModel):
    # Chat 与生图共用默认的 OpenAI-compatible 连接配置。
    provider: str

    # 默认连接配置；模块级配置可以单独覆盖。
    api_key: str
    base_url: str

    proxy: Optional[str]
    timeout: float

    # Modules
    chat: ChatConfig
    image: ImageConfig
    search: SearchConfig

    def resolve(self, module: str = "chat") -> ResolvedProviderConfig:
        """
        按 module 级别 → 全局 的优先级，合并出最终的连接参数。
        module: "chat" | "image"
        """
        mod_cfg = getattr(self, module, None)

        def _pick(field: str, default=None):
            # 优先取 module 级别的值
            if mod_cfg is not None:
                val = getattr(mod_cfg, field, None)
                if val is not None and (not isinstance(val, str) or val.strip()):
                    return val.strip() if isinstance(val, str) else val
            # 回退到全局
            val = getattr(self, field, default)
            if isinstance(val, str):
                return val.strip()
            return val

        provider = (_pick("provider") or "openai_compatible").lower()
        if provider != "openai_compatible":
            raise ValueError(f"Unsupported LLM provider: {provider}")
        api_key = _pick("api_key") or ""
        base_url = _pick("base_url") or "https://api.openai.com/v1"

        return ResolvedProviderConfig(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
        )


CONFIG_PATH = PluginPaths("ai_assistant").config / "config.json"

plugin_config = load_config("ai_assistant", PluginConfig)
