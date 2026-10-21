"""HLTV 订阅插件配置"""

from pydantic import Field

from core.settings import ConfigModel, load_config


class Config(ConfigModel):
    """HLTV 订阅插件配置"""

    # FlareSolverr 会话独立于 Bot 进程，重启 Bot 时继续复用。
    flaresolverr_url: str
    flaresolverr_timeout_seconds: int = Field(gt=0)
    # 请求本就由锁串行，0 表示上一个返回就发下一个；若频繁被 Cloudflare 拦截进入冷却，再调回几秒。
    request_interval_seconds: float = Field(ge=0)
    # 同一标签页反复加载 HLTV，Chromium 每次涨十几 MB 且不回收，抓够次数就换会话。
    session_max_requests: int = Field(gt=0)
    block_cooldown_seconds: int = Field(gt=0)
    block_cooldown_max_seconds: int = Field(gt=0)
    timezone: str  # 时区

    # 调度配置
    auto_unsub_delay_days: int = Field(ge=0)  # 赛事结束后延迟多少天自动取消订阅
    auto_sub_interval_days: int  # 自动订阅 big events 的心跳间隔；启动时另跑一轮
    notified_ttl_days: int  # 推送去重状态保留天数
    scheduler_jitter_seconds: int  # 各赛事下次检查时间的随机偏移

    # 水印配置
    watermark_text: str  # 自定义水印文本，支持换行


plugin_config = load_config("hltv_sub", Config)
