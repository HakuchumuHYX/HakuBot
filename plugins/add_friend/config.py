from core.settings import ConfigModel, load_config, update_fields
from utils.paths import PluginPaths
from nonebot import logger

CONFIG_FILE = PluginPaths("add_friend").config / "config.json"


class Config(ConfigModel):
    auto_approve_groups: list[str]
    # 人工审核模式：未命中白名单时不自动拒绝，改为通知超管用命令处理
    manual_review: bool


plugin_config = load_config("add_friend", Config)

# 自动同意好友请求的群聊列表；增删命令会原地修改这个集合
AUTO_APPROVE_GROUPS: set[str] = set(plugin_config.auto_approve_groups)
MANUAL_REVIEW: bool = plugin_config.manual_review


def save_auto_approve_groups(groups: set[str]) -> bool:
    """写回白名单群组（只改这一个字段），成功后同步内存里的集合"""
    try:
        update_fields(CONFIG_FILE, {"auto_approve_groups": sorted(groups)})
    except Exception as e:
        logger.exception(f"保存配置文件失败: {e}")
        return False
    AUTO_APPROVE_GROUPS.clear()
    AUTO_APPROVE_GROUPS.update(groups)
    logger.info(f"已保存 {len(groups)} 个自动同意群组到配置文件")
    return True


# 群号提取正则表达式模式
GROUP_PATTERNS: list[str] = [
    r"群(\d+)",
    r"来自群.?(\d+)",
    r"群.?(\d+)",
    r"group.?(\d+)",
    r"群号.?(\d+)",
    r"(\d+)",
]

# 消息模板
WELCOME_MESSAGE = "欢迎好友！"
FRIEND_APPROVED_MESSAGE = "你好！现在我们是好友了。"
REQUEST_NOTIFICATION_TEMPLATE = (
    "收到新的好友申请：\nQQ：{user_id}\n来自群：{group}\n验证信息：{comment}\n\n"
    "请使用以下命令处理：\n/同意好友 {user_id}\n/拒绝好友 {user_id}"
)

# 防重复处理配置
CACHE_EXPIRE_TIME = 300  # 5分钟
