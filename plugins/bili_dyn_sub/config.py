"""bili_dyn_sub 插件配置。

配置来源为插件目录下的 config.json（不存在则写出一份默认配置），
所有默认值对应设计文档 docs/bili_dyn_sub_design.md 的 §3.4 / §4.3 / §6。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator

from ..utils.json_io import atomic_write_json
from ..utils.tools import get_logger

logger = get_logger("bili_dyn_sub.config")

# 默认浏览器 UA：轮询与造 cookie 必须共用同一个，且绝不能含 python/httpx/curl 字样
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
# UA 中出现这些字样时 B 站连 buvid3 都不会下发，需回落到默认 UA
_FORBIDDEN_UA_TOKENS: tuple[str, ...] = ("python", "httpx", "curl", "aiohttp", "requests")


class Config(BaseModel, extra="ignore"):
    """B 站动态订阅插件配置"""

    # --- 轮询调度（§6）---
    poll_interval_seconds: int = 100  # 轮询基础间隔
    poll_jitter_seconds: int = 20  # 每轮随机抖动上限
    uid_request_gap_seconds: float = 8.0  # 同一轮内两个 UID 之间的最小间隔（串行错开）

    # --- 推送新鲜度闸门（§4.3）---
    # 这里只有一个问题：一条动态最多可以"旧"到什么程度，还值得推送？答案是一个时长。
    # "要不要补推停机期间遗漏的动态"不是一种独立模式、也不需要独立开关，
    # 它只是这个窗口取多大的自然结果：
    #   30   → 只推新鲜动态，停机期间遗漏的自然被丢弃（= 不补推，默认；重启/迁移对群友无感）
    #   1440 → 停机一天以内的动态都补上（= 补推）
    #   0    → 不做时间限制（= 全推，慎用：长时间停机后会一次性刷屏）
    # 超出窗口的动态一律静默标记已读（推进 seen 状态但不推送），因此不会在下一轮反复处理。
    # 窗口不宜小于十几分钟：B 站接口有时延迟数分钟才吐出新动态，软风控还会让某轮拉空（§11.5）。
    max_dynamic_age_minutes: int = 30

    # 与"多旧才推"无关的独立关注点：防刷屏。
    # 单 UID 单轮最多推送条数，超出的只标 seen；0 表示不限。
    # 窗口收窄到 30 分钟后，半小时内真发 5 条也应该都推出去，故默认给到 5。
    max_push_per_round: int = 5

    # --- 鉴权与取数（§3.2/§3.4）---
    sessdata: str = ""  # 可选登录 cookie，留空走匿名（L1 纯 HTTP）
    bili_jct: str = ""  # 与 sessdata 配套的 csrf token，可留空
    proxy: Optional[str] = None  # 机房 IP 被 412 时使用的代理，如 http://127.0.0.1:7890
    enable_wbi: bool = False  # wbi 签名开关，第一版关闭，大面积 352 时再启用
    enable_playwright_fallback: bool = True  # L2 Playwright 兜底造 cookie
    http_timeout_connect: float = 5.0  # 连接超时（秒）
    http_timeout_total: float = 10.0  # 总超时（秒）
    user_agent: str = DEFAULT_USER_AGENT

    # --- 去重状态裁剪（§4.1）---
    seen_ids_max: int = 50  # 每个 UID 保留的已推送动态 id 数量上限（FIFO）
    seen_retention_days: int = 14  # 已推送状态保留天数

    # --- 推送节奏与渲染（§5.2）---
    send_interval_seconds: float = 1.5  # 全局发送队列间隔
    send_retry_times: int = 3  # 单条消息发送失败重试次数
    text_truncate_length: int = 500  # 正文截断长度（超出加 "..."）

    @field_validator("user_agent")
    @classmethod
    def _check_user_agent(cls, value: str) -> str:
        """校验 UA：为空或含脚本客户端特征时回落到默认浏览器 UA"""
        stripped = value.strip()
        if not stripped:
            return DEFAULT_USER_AGENT
        lowered = stripped.lower()
        hit = next((token for token in _FORBIDDEN_UA_TOKENS if token in lowered), None)
        if hit is not None:
            logger.warning(f"配置的 user_agent 含 {hit!r} 字样，会导致 B 站拒发 cookie，已回落到默认 UA")
            return DEFAULT_USER_AGENT
        return stripped


PLUGIN_NAME = "bili_dyn_sub"
PLUGIN_DIR = Path(__file__).parent
CONFIG_FILE_PATH = PLUGIN_DIR / "config.json"

# 运行时数据目录（订阅/去重状态、cookie 缓存均在此）
DATA_DIR = Path("data/bili_dyn_sub")
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"  # 订阅 + seen_ids/游标
CREDENTIAL_FILE = DATA_DIR / "credential.json"  # cookie / ticket 缓存

# 历史字段 → 现字段的改名对照，仅用于清理旧 config.json 时给出提示（不做旧值自动迁移）
_RENAMED_FIELDS: dict[str, str] = {
    "catchup_max_count": "max_push_per_round",
}


def load_plugin_config() -> Config:
    """加载插件配置；文件缺失则写出一份默认配置，读取失败则退回默认值"""
    if not CONFIG_FILE_PATH.exists():
        logger.info(f"未找到 config.json，正在创建默认配置文件于 {CONFIG_FILE_PATH}")
        default_config = Config()
        try:
            atomic_write_json(CONFIG_FILE_PATH, default_config.model_dump(), indent=4)
        except OSError as e:
            logger.error(f"创建默认配置文件失败: {e}，本次使用内存中的默认配置")
        return default_config

    logger.info(f"正在从 {CONFIG_FILE_PATH} 加载 B 站动态订阅配置...")
    try:
        raw = json.loads(CONFIG_FILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"读取 config.json 失败: {e}，将使用默认配置")
        return Config()
    if not isinstance(raw, dict):
        logger.error("config.json 顶层不是对象，将使用默认配置")
        return Config()
    try:
        config = Config.model_validate(raw)
    except ValueError as e:
        logger.error(f"校验 config.json 失败: {e}，将使用默认配置")
        return Config()

    _sync_config_file(raw, config)
    return config


def _sync_config_file(raw: dict, config: Config) -> None:
    """把用户的 config.json 与当前配置模型做双向同步。

    - 补充：插件升级后新增的字段虽然有代码默认值，但不出现在 config.json 里就无法
      被发现和调整，按"用户已有值优先、缺失项填默认值"补齐。
    - 移除：模型里已不存在的过期字段一并清掉，否则用户的 config.json 会长期残留
      改名/废弃后的无效项，看起来像是在"缝缝补补"，也让人误以为它们还生效。

    只有确实存在增删时才回写文件，避免每次启动都无意义地重写。
    """
    field_names = set(Config.model_fields)
    missing = [name for name in Config.model_fields if name not in raw]
    obsolete = [name for name in raw if name not in field_names]
    if not missing and not obsolete:
        return

    # config 已经是"用户值 + 缺失项默认值"的合并结果（extra="ignore" 已丢弃过期字段）
    merged = config.model_dump()
    try:
        atomic_write_json(CONFIG_FILE_PATH, merged, indent=4)
    except OSError as e:
        logger.warning(f"同步 config.json 失败: {e}（本次运行使用内存中的配置，不影响功能）")
        return

    if missing:
        logger.info(f"config.json 已补充新增配置项: {', '.join(missing)}")
    if obsolete:
        logger.info(f"config.json 已移除失效配置项: {', '.join(obsolete)}")
        for old_name in obsolete:
            new_name = _RENAMED_FIELDS.get(old_name)
            if new_name is not None:
                logger.info(f"配置项 {old_name} 已改名为 {new_name}，如需自定义请改新项")


plugin_config: Config = load_plugin_config()
