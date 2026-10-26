"""bili_dyn_sub 插件配置，来自 config/bili_dyn_sub/config.json"""

from __future__ import annotations

from pydantic import Field, field_validator

from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths


# UA 中出现这些字样时 B 站连 buvid3 都不会下发；轮询与造 cookie 共用同一个 UA
_FORBIDDEN_UA_TOKENS: tuple[str, ...] = (
    "python",
    "httpx",
    "curl",
    "aiohttp",
    "requests",
)


class Config(ConfigModel):
    """B 站动态订阅插件配置"""

    poll_interval_seconds: int = Field(ge=30)
    poll_jitter_seconds: int = Field(ge=0)
    uid_request_gap_seconds: float = Field(ge=0)  # 同一轮内两个 UID 之间的间隔

    # 动态最多旧到多少分钟还推送；超出的静默标记已读，0 表示不限。
    # 停机期间的积压靠它丢弃，所以不补推不需要单独开关。
    # 不宜小于十几分钟：B 站接口有时延迟数分钟才吐出新动态。
    max_dynamic_age_minutes: int = Field(ge=0)
    # 单 UID 单轮最多推送条数（防刷屏），超出的只标已读并提示群友，0 表示不限
    max_push_per_round: int = Field(ge=0)

    sessdata: str  # 可选登录 cookie，留空走匿名
    proxy: str | None  # 机房 IP 被 412 时使用的代理
    http_timeout_connect: float = Field(ge=1)
    http_timeout_total: float = Field(ge=1)
    user_agent: str

    send_interval_seconds: float = Field(ge=0)
    send_retry_times: int = Field(ge=1)
    text_truncate_length: int = Field(gt=0)

    @field_validator("user_agent")
    @classmethod
    def _check_user_agent(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("user_agent 不能为空")
        lowered = stripped.lower()
        hit = next((token for token in _FORBIDDEN_UA_TOKENS if token in lowered), None)
        if hit is not None:
            raise ValueError(f"user_agent 含 {hit!r} 字样，B 站会拒发 cookie")
        return stripped


DATA_DIR = PluginPaths("bili_dyn_sub").data
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"  # 订阅 + 游标
CREDENTIAL_FILE = DATA_DIR / "credential.json"  # cookie 缓存

plugin_config: Config = load_config("bili_dyn_sub", Config)
