from pathlib import Path
from typing import Optional
import json

from pydantic import BaseModel, Field


class StrictBaseModel(BaseModel):
    class Config:
        extra = "forbid"


class ChatConfig(StrictBaseModel):
    # --- Per-module provider override (留空则回退到全局配置) ---
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    model: str = "gpt-3.5-turbo"
    max_tokens: Optional[int] = 8192
    thinking_enabled: bool = False
    reasoning_effort: Optional[str] = None
    extra_body: dict = Field(default_factory=dict)
    system_prompt: str = (
        "你是HakuBot的AI助手。请遵守以下回复规范：\n"
        "1. 语气活泼、亲切、自然，像朋友聊天一样，避免生硬的机器感。\n"
        "2. 回答要充实、有内容，给出足够的细节和解释，不要过于简短或惜字如金。\n"
        "3. 如果问题涉及多个方面，请分点或分段回答，保持条理清晰。\n"
        "4. 善用 Markdown 格式（标题、列表、代码块等）来组织长回复，提升可读性。\n"
        "5. 在回答技术问题时，给出具体示例或代码片段会更好。\n"
        "6. 如果不确定答案，坦诚说明，不要编造信息。"
    )
    # 采样温度，控制回复的随机性/创造性（0.0~2.0，Claude 建议 0.7 左右）
    temperature: Optional[float] = 0.7
    # nucleus sampling，与 temperature 二选一微调即可
    top_p: Optional[float] = None
    # Claude 特有的 assistant prefill：在 messages 末尾追加一条 assistant 消息作为回复开头引导
    # 模型会接着这段文字继续生成，可有效引导输出风格和详细度
    # 留空则不启用；示例值："好的，让我来详细回答你的问题：\n\n"
    assistant_prefill: Optional[str] = None
    # 图片最大边长（像素），超过此值会等比缩放+JPEG压缩，以减少视觉API的token消耗
    # 设为 0 或 None 则不压缩
    image_max_size: int = 1536
    watermark: str = ""
    # 图片回复的背景颜色，默认为浅灰色（护眼白），例如也可用绿豆沙色 #C7EDCC 等
    bg_color: str = "#f8f9fa"


class ImageConfig(StrictBaseModel):
    # --- Per-module provider override (留空则回退到全局配置) ---
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

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


class SearchConfig(StrictBaseModel):
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


class ResolvedProviderConfig:
    """resolve() 返回的连接参数集合，供 service 层直接使用。"""
    __slots__ = ("provider", "api_key", "base_url")

    def __init__(self, provider: str, api_key: str, base_url: str):
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url


class PluginConfig(StrictBaseModel):
    # --- Provider Switch ---
    # openai_compatible: 走 /chat/completions 的 OpenAI 兼容接口（保持现状）
    provider: str = "openai_compatible"

    # OpenAI compatible config (legacy / default)
    api_key: str
    base_url: str = "https://api.openai.com/v1"

    proxy: Optional[str] = None
    timeout: float = 60.0

    # Modules
    chat: ChatConfig = ChatConfig()
    image: ImageConfig = ImageConfig()
    search: SearchConfig = SearchConfig()

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


CURRENT_PATH = Path(__file__).parent
CONFIG_PATH = CURRENT_PATH / "config.json"


def load_config() -> PluginConfig:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件未找到: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    return PluginConfig(**data)


def save_config(config: PluginConfig):
    """保存配置到文件"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        # 使用 dict() 以兼容 Pydantic V1/V2，确保中文不乱码
        json.dump(config.dict(), f, indent=4, ensure_ascii=False)


plugin_config = load_config()
