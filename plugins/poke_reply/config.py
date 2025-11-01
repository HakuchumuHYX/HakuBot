from pathlib import Path
from typing import List, Set
import json
from nonebot import logger

# 文件路径配置
PLUGIN_DIR = Path(__file__).parent
TEXT_FILES_DIR = PLUGIN_DIR / "text_files"  # 改为目录存储多个文件
IMAGE_FILES_DIR = PLUGIN_DIR / "image_files"  # 新增：图片存储目录
CONFIG_FILES_DIR = PLUGIN_DIR / "config_files"  # 新增：配置文件目录

# 确保目录存在
TEXT_FILES_DIR.mkdir(exist_ok=True)
IMAGE_FILES_DIR.mkdir(exist_ok=True)
CONFIG_FILES_DIR.mkdir(exist_ok=True)

# 配置文件路径
POKE_CD_GROUPS_FILE = CONFIG_FILES_DIR / "poke_cd_groups.json"
TEXT_TO_IMAGE_GROUPS_FILE = CONFIG_FILES_DIR / "text_to_image_groups.json"

# 插件配置
SIMILARITY_THRESHOLD = 0.6  # 相似度阈值
MAX_TEXT_LENGTH = 1000  # 最大文本长度
CONTRIBUTE_COMMAND_PRIORITY = 5  # 投稿命令优先级

# 文本转图片配置
TEXT_TO_IMAGE_LENGTH_THRESHOLD = 200  # 文本长度阈值，超过此长度转为图片
TEXT_TO_IMAGE_COMMAND_PRIORITY = 6  # 文本转图片命令优先级

# 戳一戳CD配置
POKE_CD_TIME = 30  # CD时间，单位：秒

# 默认回复文本
DEFAULT_TEXTS = [
    "ERROR! text.json失踪了喵！",
    "ERROR! text.json是空的喵！",
    "ERROR! text.json解析错误喵！",
    "ERROR! text.json加载错误喵！"
]

# 默认启用的群组（仅在首次启动时使用）
DEFAULT_POKE_CD_GROUPS = {}
DEFAULT_TEXT_TO_IMAGE_GROUPS = {}

# 全局变量，存储启用的群组
POKE_CD_ENABLED_GROUPS: Set[int] = set()
TEXT_TO_IMAGE_ENABLED_GROUPS: Set[int] = set()


def load_config_groups():
    """加载启用的群组配置"""
    global POKE_CD_ENABLED_GROUPS, TEXT_TO_IMAGE_ENABLED_GROUPS

    # 加载戳一戳CD群组
    if POKE_CD_GROUPS_FILE.exists():
        try:
            with open(POKE_CD_GROUPS_FILE, 'r', encoding='utf-8') as f:
                POKE_CD_ENABLED_GROUPS = set(json.load(f))
            logger.info(f"已加载戳一戳CD群组配置: {len(POKE_CD_ENABLED_GROUPS)} 个群组")
        except Exception as e:
            logger.error(f"加载戳一戳CD群组配置失败: {e}")
            POKE_CD_ENABLED_GROUPS = DEFAULT_POKE_CD_GROUPS.copy()
    else:
        # 文件不存在，使用默认配置并保存
        POKE_CD_ENABLED_GROUPS = DEFAULT_POKE_CD_GROUPS.copy()
        save_poke_cd_groups()

    # 加载文本转图片群组
    if TEXT_TO_IMAGE_GROUPS_FILE.exists():
        try:
            with open(TEXT_TO_IMAGE_GROUPS_FILE, 'r', encoding='utf-8') as f:
                TEXT_TO_IMAGE_ENABLED_GROUPS = set(json.load(f))
            logger.info(f"已加载文本转图片群组配置: {len(TEXT_TO_IMAGE_ENABLED_GROUPS)} 个群组")
        except Exception as e:
            logger.error(f"加载文本转图片群组配置失败: {e}")
            TEXT_TO_IMAGE_ENABLED_GROUPS = DEFAULT_TEXT_TO_IMAGE_GROUPS.copy()
    else:
        # 文件不存在，使用默认配置并保存
        TEXT_TO_IMAGE_ENABLED_GROUPS = DEFAULT_TEXT_TO_IMAGE_GROUPS.copy()
        save_text_to_image_groups()


def save_poke_cd_groups():
    """保存戳一戳CD群组配置"""
    try:
        with open(POKE_CD_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(POKE_CD_ENABLED_GROUPS), f, ensure_ascii=False, indent=2)
        logger.debug(f"已保存戳一戳CD群组配置: {len(POKE_CD_ENABLED_GROUPS)} 个群组")
    except Exception as e:
        logger.error(f"保存戳一戳CD群组配置失败: {e}")


def save_text_to_image_groups():
    """保存文本转图片群组配置"""
    try:
        with open(TEXT_TO_IMAGE_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(TEXT_TO_IMAGE_ENABLED_GROUPS), f, ensure_ascii=False, indent=2)
        logger.debug(f"已保存文本转图片群组配置: {len(TEXT_TO_IMAGE_ENABLED_GROUPS)} 个群组")
    except Exception as e:
        logger.error(f"保存文本转图片群组配置失败: {e}")


def get_group_text_path(group_id: int) -> Path:
    """根据群号获取对应的text.json文件路径"""
    return TEXT_FILES_DIR / f"text_{group_id}.json"


def get_group_image_dir(group_id: int) -> Path:
    """根据群号获取对应的图片存储目录"""
    group_dir = IMAGE_FILES_DIR / f"group_{group_id}"
    group_dir.mkdir(exist_ok=True)
    return group_dir


def get_group_image_list_path(group_id: int) -> Path:
    """根据群号获取对应的图片列表文件路径"""
    return IMAGE_FILES_DIR / f"images_{group_id}.json"


def add_text_to_image_group(group_id: int) -> None:
    """添加群组到文本转图片启用列表"""
    TEXT_TO_IMAGE_ENABLED_GROUPS.add(group_id)
    save_text_to_image_groups()


def remove_text_to_image_group(group_id: int) -> None:
    """从文本转图片启用列表中移除群组"""
    TEXT_TO_IMAGE_ENABLED_GROUPS.discard(group_id)
    save_text_to_image_groups()


def is_text_to_image_enabled(group_id: int) -> bool:
    """检查群组是否启用文本转图片功能"""
    return group_id in TEXT_TO_IMAGE_ENABLED_GROUPS


def set_text_to_image_threshold(threshold: int) -> None:
    """设置文本转图片阈值"""
    global TEXT_TO_IMAGE_LENGTH_THRESHOLD
    TEXT_TO_IMAGE_LENGTH_THRESHOLD = threshold


def get_text_to_image_threshold() -> int:
    """获取文本转图片阈值"""
    return TEXT_TO_IMAGE_LENGTH_THRESHOLD


def add_poke_cd_group(group_id: int) -> None:
    """添加群组到戳一戳CD启用列表"""
    POKE_CD_ENABLED_GROUPS.add(group_id)
    save_poke_cd_groups()


def remove_poke_cd_group(group_id: int) -> None:
    """从戳一戳CD启用列表中移除群组"""
    POKE_CD_ENABLED_GROUPS.discard(group_id)
    save_poke_cd_groups()


def is_poke_cd_enabled(group_id: int) -> bool:
    """检查群组是否启用戳一戳CD功能"""
    return group_id in POKE_CD_ENABLED_GROUPS


def set_poke_cd_time(cd_time: int) -> None:
    """设置戳一戳CD时间"""
    global POKE_CD_TIME
    POKE_CD_TIME = cd_time


def get_poke_cd_time() -> int:
    """获取戳一戳CD时间"""
    return POKE_CD_TIME


def get_poke_cd_enabled_groups() -> Set[int]:
    """获取所有启用戳一戳CD的群组"""
    return POKE_CD_ENABLED_GROUPS.copy()


def get_text_to_image_enabled_groups() -> Set[int]:
    """获取所有启用文本转图片的群组"""
    return TEXT_TO_IMAGE_ENABLED_GROUPS.copy()


# 在模块加载时自动加载配置
load_config_groups()