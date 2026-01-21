from pathlib import Path
from typing import Set, Dict, List
import json
from nonebot import logger
import nonebot_plugin_localstore as localstore

# --- 基础路径配置 ---
PLUGIN_NAME = "poke_reply"
PLUGIN_DIR = Path(__file__).parent
DATA_DIR = localstore.get_data_dir(PLUGIN_NAME)

# 创建必要的目录
DATA_DIR.mkdir(parents=True, exist_ok=True)
TEXT_FILES_DIR = DATA_DIR / "text_files"
IMAGE_FILES_DIR = DATA_DIR / "image_files"
CONFIG_FILES_DIR = DATA_DIR / "config_files"

TEXT_FILES_DIR.mkdir(exist_ok=True)
IMAGE_FILES_DIR.mkdir(exist_ok=True)
CONFIG_FILES_DIR.mkdir(exist_ok=True)

# --- 缓存文件路径 ---
MESSAGE_CACHE_FILE = DATA_DIR / "message_cache.json"
TEXT_IMAGE_CACHE_FILE = DATA_DIR / "text_image_cache.json"
DELETE_REQUESTS_FILE = DATA_DIR / "delete_requests.json"
IMAGE_HASH_CACHE_FILE = DATA_DIR / "image_hash_cache.json"

# --- 配置文件路径 ---
TEXT_TO_IMAGE_GROUPS_FILE = CONFIG_FILES_DIR / "text_to_image_groups.json"

# --- 常量配置 ---
# 文本相似度阈值
SIMILARITY_THRESHOLD = 0.6
# 图片相似度阈值
IMAGE_SIMILARITY_THRESHOLD = 50
# 最大文本长度
MAX_TEXT_LENGTH = 1000
# 投稿命令优先级
CONTRIBUTE_COMMAND_PRIORITY = 5
# 文本转图片默认长度阈值
DEFAULT_TEXT_TO_IMAGE_THRESHOLD = 200
TEXT_TO_IMAGE_COMMAND_PRIORITY = 6
# 缓存过期时间 (秒)
CACHE_EXPIRE_TIME = 10 * 60  # 10分钟
IMAGE_HASH_CACHE_TTL = 30 * 24 * 60 * 60  # 30天
# 图片哈希缓存版本
CACHE_VERSION = "1.0_poke_reply"

# --- 默认文本 ---
DEFAULT_TEXTS = [
    "ERROR! text.json失踪了喵！",
    "ERROR! text.json是空的喵！",
    "ERROR! text.json解析错误喵！",
    "ERROR! text.json加载错误喵！"
]

# --- 运行时配置 ---
TEXT_TO_IMAGE_ENABLED_GROUPS: Set[int] = set()
TEXT_TO_IMAGE_LENGTH_THRESHOLD = DEFAULT_TEXT_TO_IMAGE_THRESHOLD
DEFAULT_TEXT_TO_IMAGE_GROUPS = {}

# --- 配置加载与保存函数 ---

def load_config():
    """加载所有配置"""
    global TEXT_TO_IMAGE_ENABLED_GROUPS
    
    # 加载文本转图片群组
    if TEXT_TO_IMAGE_GROUPS_FILE.exists():
        try:
            with open(TEXT_TO_IMAGE_GROUPS_FILE, 'r', encoding='utf-8') as f:
                TEXT_TO_IMAGE_ENABLED_GROUPS = set(json.load(f))
            logger.info(f"已加载文本转图片群组配置: {len(TEXT_TO_IMAGE_ENABLED_GROUPS)} 个群组")
        except Exception as e:
            logger.error(f"加载文本转图片群组配置失败: {e}")
            TEXT_TO_IMAGE_ENABLED_GROUPS = DEFAULT_TEXT_TO_IMAGE_GROUPS.copy()
            save_text_to_image_groups()
    else:
        TEXT_TO_IMAGE_ENABLED_GROUPS = DEFAULT_TEXT_TO_IMAGE_GROUPS.copy()
        save_text_to_image_groups()

def save_text_to_image_groups():
    """保存文本转图片群组配置"""
    try:
        with open(TEXT_TO_IMAGE_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(TEXT_TO_IMAGE_ENABLED_GROUPS), f, ensure_ascii=False, indent=2)
        logger.debug(f"已保存文本转图片群组配置: {len(TEXT_TO_IMAGE_ENABLED_GROUPS)} 个群组")
    except Exception as e:
        logger.error(f"保存文本转图片群组配置失败: {e}")

# --- 路径获取辅助函数 ---

def get_group_text_path(group_id: int) -> Path:
    return TEXT_FILES_DIR / f"text_{group_id}.json"

def get_group_image_dir(group_id: int) -> Path:
    group_dir = IMAGE_FILES_DIR / f"group_{group_id}"
    group_dir.mkdir(exist_ok=True)
    return group_dir

def get_group_image_list_path(group_id: int) -> Path:
    return IMAGE_FILES_DIR / f"images_{group_id}.json"

# --- 配置操作辅助函数 ---

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

def get_text_to_image_enabled_groups() -> Set[int]:
    return TEXT_TO_IMAGE_ENABLED_GROUPS.copy()

# 初始化加载
load_config()
