"""bili_dyn_sub 插件配置。

配置来自 config/plugins/bili_dyn_sub/config.json，
所有默认值对应设计文档 docs/bili_dyn_sub_design.md 的 §3.4 / §4.3 / §6。
"""

from __future__ import annotations
from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator


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

    # --- 轮询调度（§6）---
    poll_interval_seconds: int = Field(ge=30)  # 轮询基础间隔
    poll_jitter_seconds: int = Field(ge=0)  # 每轮随机抖动上限
    uid_request_gap_seconds: float = Field(
        ge=0
    )  # 同一轮内两个 UID 之间的最小间隔（串行错开）

    # --- 推送新鲜度闸门（§4.3）---
    # 这里只有一个问题：一条动态最多可以"旧"到什么程度，还值得推送？答案是一个时长。
    # "要不要补推停机期间遗漏的动态"不是一种独立模式、也不需要独立开关，
    # 它只是这个窗口取多大的自然结果：
    #   30   → 只推新鲜动态，停机期间遗漏的自然被丢弃（= 不补推，默认；重启/迁移对群友无感）
    #   1440 → 停机一天以内的动态都补上（= 补推）
    #   0    → 不做时间限制（= 全推，慎用：长时间停机后会一次性刷屏）
    # 超出窗口的动态一律静默标记已读（推进 seen 状态但不推送），因此不会在下一轮反复处理。
    # 窗口不宜小于十几分钟：B 站接口有时延迟数分钟才吐出新动态，软风控还会让某轮拉空（§11.5）。
    max_dynamic_age_minutes: int = Field(ge=0)

    # 与"多旧才推"无关的独立关注点：防刷屏。
    # 单 UID 单轮最多推送条数，超出的只标 seen；0 表示不限。
    # 窗口收窄到 30 分钟后，半小时内真发 5 条也应该都推出去，故默认给到 5。
    max_push_per_round: int = Field(ge=0)

    # --- 鉴权与取数（§3.2/§3.4）---
    sessdata: str  # 可选登录 cookie，留空走匿名（L1 纯 HTTP）
    bili_jct: str  # 与 sessdata 配套的 csrf token，可留空
    proxy: Optional[str]  # 机房 IP 被 412 时使用的代理，如 http://127.0.0.1:7890
    enable_wbi: bool  # wbi 签名开关，第一版关闭，大面积 352 时再启用
    enable_playwright_fallback: bool  # L2 Playwright 兜底造 cookie
    http_timeout_connect: float = Field(ge=1)  # 连接超时（秒）
    http_timeout_total: float = Field(ge=1)  # 总超时（秒）
    user_agent: str

    # --- 去重状态裁剪（§4.1）---
    seen_ids_max: int = Field(ge=1)  # 每个 UID 保留的已推送动态 id 数量上限（FIFO）
    seen_retention_days: int = Field(ge=1)  # 已推送状态保留天数

    # --- 推送节奏与渲染（§5.2）---
    send_interval_seconds: float = Field(ge=0)  # 全局发送队列间隔
    send_retry_times: int = Field(ge=1)  # 单条消息发送失败重试次数
    text_truncate_length: int = Field(gt=0)  # 正文截断长度（超出加 "..."）

    @field_validator("user_agent")
    @classmethod
    def _check_user_agent(cls, value: str) -> str:
        """校验 UA：不能为空，也不能带脚本客户端特征"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("user_agent 不能为空")
        lowered = stripped.lower()
        hit = next((token for token in _FORBIDDEN_UA_TOKENS if token in lowered), None)
        if hit is not None:
            raise ValueError(f"user_agent 含 {hit!r} 字样，B 站会拒发 cookie")
        return stripped


PLUGIN_NAME = "bili_dyn_sub"
PLUGIN_DIR = Path(__file__).parent

# 运行时数据目录（订阅/去重状态、cookie 缓存均在此）
DATA_DIR = PluginPaths("bili_dyn_sub").data
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"  # 订阅 + seen_ids/游标
CREDENTIAL_FILE = DATA_DIR / "credential.json"  # cookie / ticket 缓存

plugin_config: Config = load_config("bili_dyn_sub", Config)
