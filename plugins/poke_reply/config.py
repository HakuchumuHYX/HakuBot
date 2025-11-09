# poke_reply/config.py
from pathlib import Path
from typing import List, Set
import json
from nonebot import logger
import nonebot_plugin_localstore as localstore

# 文件路径配置
PLUGIN_DIR = Path(__file__).parent
PLUGIN_NAME = "poke_reply"
data_dir = localstore.get_data_dir(PLUGIN_NAME)
data_dir.mkdir(parents=True, exist_ok=True)
TEXT_FILES_DIR = data_dir / "text_files"
IMAGE_FILES_DIR = data_dir / "image_files"
CONFIG_FILES_DIR = data_dir / "config_files"

# 确保目录存在
TEXT_FILES_DIR.mkdir(exist_ok=True)
IMAGE_FILES_DIR.mkdir(exist_ok=True)
CONFIG_FILES_DIR.mkdir(exist_ok=True)

# 配置文件路径
# (POKE_CD_GROUPS_FILE 已删除)
TEXT_TO_IMAGE_GROUPS_FILE = CONFIG_FILES_DIR / "text_to_image_groups.json"

# 插件配置
SIMILARITY_THRESHOLD = 0.6
MAX_TEXT_LENGTH = 1000
CONTRIBUTE_COMMAND_PRIORITY = 5
IMAGE_SIMILARITY_THRESHOLD = 50

# 文本转图片配置
TEXT_TO_IMAGE_LENGTH_THRESHOLD = 200
TEXT_TO_IMAGE_COMMAND_PRIORITY = 6

# (POKE_CD_TIME 已删除)

# 默认回复文本
DEFAULT_TEXTS = [
    "ERROR! text.json失踪了喵！",
    "ERROR! text.json是空的喵！",
    "ERROR! text.json解析错误喵！",
    "ERROR! text.json加载错误喵！"
]

# 默认启用的群组
# (DEFAULT_POKE_CD_GROUPS 已删除)
DEFAULT_TEXT_TO_IMAGE_GROUPS = {}

# 全局变量，存储启用的群组
# (POKE_CD_ENABLED_GROUPS 已删除)
TEXT_TO_IMAGE_ENABLED_GROUPS: Set[int] = set()


def load_config_groups():
    """加载启用的群组配置"""
    global TEXT_TO_IMAGE_ENABLED_GROUPS

    # (加载戳一戳CD群组部分 已删除)

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
        TEXT_TO_IMAGE_ENABLED_GROUPS = DEFAULT_TEXT_TO_IMAGE_GROUPS.copy()
        save_text_to_image_groups()


# (save_poke_cd_groups 已删除)

def save_text_to_image_groups():
    """保存文本转图片群组配置"""
    try:
        with open(TEXT_TO_IMAGE_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(TEXT_TO_IMAGE_ENABLED_GROUPS), f, ensure_ascii=False, indent=2)
        logger.debug(f"已保存文本转图片群组配置: {len(TEXT_TO_IMAGE_ENABLED_GROUPS)} 个群组")
    except Exception as e:
        logger.error(f"保存文本转图片群组配置失败: {e}")


def get_group_text_path(group_id: int) -> Path:
    return TEXT_FILES_DIR / f"text_{group_id}.json"


def get_group_image_dir(group_id: int) -> Path:
    group_dir = IMAGE_FILES_DIR / f"group_{group_id}"
    group_dir.mkdir(exist_ok=True)
    return group_dir


def get_group_image_list_path(group_id: int) -> Path:
    return IMAGE_FILES_DIR / f"images_{group_id}.json"


def add_text_to_image_group(group_id: int) -> None:
    TEXT_TO_IMAGE_ENABLED_GROUPS.add(group_id)
    save_text_to_image_groups()


def remove_text_to_image_group(group_id: int) -> None:
    TEXT_TO_IMAGE_ENABLED_GROUPS.discard(group_id)
    save_text_to_image_groups()


def is_text_to_image_enabled(group_id: int) -> bool:
    return group_id in TEXT_TO_IMAGE_ENABLED_GROUPS


def set_text_to_image_threshold(threshold: int) -> None:
    global TEXT_TO_IMAGE_LENGTH_THRESHOLD
    TEXT_TO_IMAGE_LENGTH_THRESHOLD = threshold


def get_text_to_image_threshold() -> int:
    return TEXT_TO_IMAGE_LENGTH_THRESHOLD


# (所有 poke_cd 相关函数 已删除)

def get_text_to_image_enabled_groups() -> Set[int]:
    return TEXT_TO_IMAGE_ENABLED_GROUPS.copy()


# 在模块加载时自动加载配置
load_config_groups()