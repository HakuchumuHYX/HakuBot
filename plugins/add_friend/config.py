from typing import List, Set
import json
import os
from pathlib import Path
from nonebot.log import logger

from ..utils.json_io import atomic_write_json

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "config.json"

def load_auto_approve_groups() -> Set[str]:
    """从 config.json 加载自动同意群组列表"""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                groups = set(config_data.get("auto_approve_groups", []))
                logger.info(f"从配置文件加载了 {len(groups)} 个自动同意群组")
                return groups
        else:
            # 如果配置文件不存在，创建默认配置
            default_config = {"auto_approve_groups": [], "manual_review": False}
            atomic_write_json(CONFIG_FILE, default_config, indent=4)
            logger.warning("配置文件不存在，已创建默认配置文件")
            return set(default_config["auto_approve_groups"])
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}，使用默认配置")
        return set()

def load_manual_review() -> bool:
    """从 config.json 加载人工审核开关（未命中白名单时是否交由超管手动处理）"""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                return bool(config_data.get("manual_review", False))
        return False
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}，manual_review 使用默认值 False")
        return False

def save_auto_approve_groups(groups: Set[str]) -> bool:
    """保存自动同意群组列表到配置文件（保留 manual_review 等其他配置项）"""
    try:
        config_data = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
            except Exception as e:
                logger.warning(f"读取原配置文件失败: {e}，将重建配置文件")
                config_data = {}
        config_data["auto_approve_groups"] = list(groups)
        config_data.setdefault("manual_review", False)
        atomic_write_json(CONFIG_FILE, config_data, indent=4)
        logger.info(f"已保存 {len(groups)} 个自动同意群组到配置文件")
        return True
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        return False

# 自动同意好友请求的群聊列表（从配置文件加载）
AUTO_APPROVE_GROUPS: Set[str] = load_auto_approve_groups()

# 人工审核模式：未命中白名单时不自动拒绝，改为通知超管用命令处理（从配置文件加载）
MANUAL_REVIEW: bool = load_manual_review()

# 群号提取正则表达式模式
GROUP_PATTERNS: List[str] = [
    r'群(\d+)',
    r'来自群.?(\d+)',
    r'群.?(\d+)',
    r'group.?(\d+)',
    r'群号.?(\d+)',
    r'(\d+)'
]

# 命令优先级
FRIEND_REQUEST_PRIORITY = 1
COMMAND_PRIORITY = 1

# 消息模板
WELCOME_MESSAGE = "欢迎好友！"
FRIEND_APPROVED_MESSAGE = "你好！现在我们是好友了。"
REJECT_MESSAGE = "您的验证信息错误！请检查验证信息中是否包含本群群号或本群群号是否在白名单中。若发现错误，请联系bot主"
REQUEST_NOTIFICATION_TEMPLATE = (
    "收到新的好友申请：\nQQ：{user_id}\n来自群：{group}\n验证信息：{comment}\n\n"
    "请使用以下命令处理：\n/同意好友 {user_id}\n/拒绝好友 {user_id}"
)

# 防重复处理配置
CACHE_EXPIRE_TIME = 300  # 5分钟