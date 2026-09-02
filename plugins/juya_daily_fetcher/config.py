from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.json_io import atomic_write_json
from ..utils.tools import get_logger

logger = get_logger("juya_daily_fetcher.config")

PLUGIN_NAME = "juya_daily_fetcher"
PLUGIN_DIR = Path(__file__).parent
CONFIG_FILE_PATH = PLUGIN_DIR / "config.json"
DATA_DIR = Path("data/juya_daily_fetcher")
STATE_FILE = DATA_DIR / "state.json"
ARTICLE_DIR = DATA_DIR / "articles"
TEMPLATE_DIR = PLUGIN_DIR / "templates"
AI_ASSISTANT_CONFIG_PATH = PLUGIN_DIR.parent / "ai_assistant" / "config.json"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LLMConfig(StrictModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: float = 120.0
    proxy: Optional[str] = None
    max_tokens: Optional[int] = 1024
    thinking_enabled: bool = False
    reasoning_effort: Optional[str] = None
    extra_body: dict = Field(default_factory=dict)


class Config(StrictModel):
    feed_url: str = "https://daily.juya.uk/rss.xml"
    targets: list[str] = Field(default_factory=list)
    debug_user_id: str = ""
    poll_interval_minutes: int = 15
    pending_max_age_minutes: int = 60
    pending_max_failures: int = 3
    article_retention_days: int = 7
    llm: LLMConfig = Field(default_factory=LLMConfig)

    @field_validator("targets")
    @classmethod
    def _normalize_targets(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("debug_user_id", "feed_url")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    def llm_ready(self) -> bool:
        return bool(self.llm.api_key.strip() and self.llm.base_url.strip() and self.llm.model.strip())


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _apply_llm_fallback(config: Config) -> Config:
    """本插件 LLM 未配齐时，回落到 ai_assistant/config.json（只改内存，不回写）。"""
    if config.llm_ready():
        return config
    if not AI_ASSISTANT_CONFIG_PATH.exists():
        logger.info("LLM 未配置，将跳过导语")
        return config

    try:
        raw = json.loads(AI_ASSISTANT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取 ai_assistant 配置失败: {e}，将跳过导语")
        return config
    if not isinstance(raw, dict):
        logger.warning("ai_assistant 配置顶层不是对象，将跳过导语")
        return config

    chat = raw.get("chat") if isinstance(raw.get("chat"), dict) else {}
    llm = config.llm
    llm.api_key = llm.api_key.strip() or _first_text(chat.get("api_key"), raw.get("api_key"))
    llm.base_url = llm.base_url.strip() or _first_text(chat.get("base_url"), raw.get("base_url"))
    llm.model = llm.model.strip() or _first_text(chat.get("model"))
    if llm.proxy is None:
        proxy = raw.get("proxy")
        llm.proxy = str(proxy).strip() if proxy else None
    if llm.max_tokens is None:
        llm.max_tokens = chat.get("max_tokens", 65536)
    if not llm.thinking_enabled:
        llm.thinking_enabled = bool(chat.get("thinking_enabled", False))
    if llm.reasoning_effort is None:
        effort = chat.get("reasoning_effort")
        llm.reasoning_effort = str(effort) if effort else None
    if not llm.extra_body:
        extra_body = chat.get("extra_body")
        llm.extra_body = extra_body if isinstance(extra_body, dict) else {}
    timeout = raw.get("timeout")
    if timeout is not None:
        try:
            llm.timeout = float(timeout)
        except (TypeError, ValueError):
            pass

    if config.llm_ready():
        logger.info("已从 ai_assistant 插件加载 LLM 配置")
    else:
        logger.info("LLM 未配置，将跳过导语")
    return config


def load_plugin_config() -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE_PATH.exists():
        logger.info(f"未找到 config.json，正在创建默认配置文件于 {CONFIG_FILE_PATH}")
        default_config = Config()
        try:
            atomic_write_json(CONFIG_FILE_PATH, default_config.model_dump(), indent=4)
        except OSError as e:
            logger.error(f"创建默认配置文件失败: {e}，本次使用内存中的默认配置")
        return _apply_llm_fallback(default_config)

    try:
        raw = json.loads(CONFIG_FILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"读取 config.json 失败: {e}，将使用默认配置（不覆盖原文件）")
        return _apply_llm_fallback(Config())
    if not isinstance(raw, dict):
        logger.error("config.json 顶层不是对象，将使用默认配置（不覆盖原文件）")
        return _apply_llm_fallback(Config())
    try:
        config = Config.model_validate(raw)
    except ValueError as e:
        logger.error(f"校验 config.json 失败: {e}，将使用默认配置（不覆盖原文件）")
        return _apply_llm_fallback(Config())
    _sync_config_file(raw, config)
    return _apply_llm_fallback(config)


def _sync_config_file(raw: dict, config: Config) -> None:
    """补齐新增字段、移除模型里已经不存在的项；有增删才回写。"""
    field_names = set(Config.model_fields)
    missing = [name for name in Config.model_fields if name not in raw]
    obsolete = [name for name in raw if name not in field_names]
    if not missing and not obsolete:
        return

    try:
        atomic_write_json(CONFIG_FILE_PATH, config.model_dump(), indent=4)
    except OSError as e:
        logger.warning(f"同步 config.json 失败: {e}（本次运行使用内存中的配置，不影响功能）")
        return

    if missing:
        logger.info(f"config.json 已补充新增配置项: {', '.join(missing)}")
    if obsolete:
        logger.info(f"config.json 已移除失效配置项: {', '.join(obsolete)}")


plugin_config = load_plugin_config()
