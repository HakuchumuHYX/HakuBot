# waifu/__init__.py
from nonebot import require
from nonebot.plugin.on import on_command
from nonebot.log import logger
import nonebot
import os
import time
from pathlib import Path

from .config import Config
from nonebot.plugin import PluginMetadata
from nonebot import get_loaded_plugins
from nonebot.plugin import Plugin
# 导入 common 中的 rule
from ..utils.common import create_exact_command_rule

# vvvvvv 插件管理 API vvvvvv
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import GroupMessageEvent

try:
    # 导入功能开关API
    from ..plugin_manager.enable import is_plugin_enabled as check_plugin
    from ..plugin_manager.enable import is_feature_enabled
    # 导入CD管理API
    from ..plugin_manager.cd_manager import check_cd, update_cd
    PLUGIN_MANAGER_LOADED = True
except ImportError:
    PLUGIN_MANAGER_LOADED = False
    # 定义回退函数
    def check_plugin(plugin_name: str, group_id: str, user_id: str) -> bool:
        return True
    def is_feature_enabled(plugin_name: str, feature_name: str, group_id: str, user_id: str) -> bool:
        return True
    def check_cd(plugin_id: str, group_id: str, user_id: str) -> int:
        return 0
    def update_cd(plugin_id: str, group_id: str, user_id: str):
        pass
# ^^^^^^ 插件管理 API 结束 ^^^^^^

# 插件标识符
PLUGIN_NAME = "groupmate_waifu"

__plugin_meta__ = PluginMetadata(
    name="娶群友",
    description="娶群友",
    usage="娶群友",
    config=Config,
    extra={}
)

# 加载全局配置
global_config = nonebot.get_driver().config
waifu_config = Config.parse_obj(global_config.dict())

# 将配置项导出，供 marry.py 和 yinpa.py 使用
waifu_save = waifu_config.waifu_save
waifu_reset = waifu_config.waifu_reset
last_sent_time_filter = waifu_config.waifu_last_sent_time_filter
HE = waifu_config.waifu_he
BE = HE + waifu_config.waifu_be
NTR = waifu_config.waifu_ntr
yinpa_HE = waifu_config.yinpa_he
yinpa_BE = yinpa_HE + waifu_config.yinpa_be
yinpa_CP = waifu_config.yinpa_cp
yinpa_CP = yinpa_HE if yinpa_CP == 0 else yinpa_CP

# 判断文件时效
timestr = time.strftime('%Y-%m-%d', time.localtime(time.time()))
timeArray = time.strptime(timestr, '%Y-%m-%d')
Zero_today = time.mktime(timeArray)

# --- 插件管理规则 (供其他模块导入) ---
def is_plugin_enabled_internal(group_id: str, user_id: str) -> bool:
    try:
        return check_plugin(PLUGIN_NAME, group_id, user_id)
    except (ImportError, TypeError):
        return True

async def check_plugin_enabled(event: GroupMessageEvent) -> bool:
    return is_plugin_enabled_internal(str(event.group_id), str(event.user_id))

def is_yinpa_enabled_internal(group_id: str, user_id: str) -> bool:
    try:
        return is_feature_enabled(PLUGIN_NAME, "yinpa", group_id, user_id)
    except (ImportError, TypeError):
        return True

async def check_yinpa_enabled(event: GroupMessageEvent) -> bool:
    return is_yinpa_enabled_internal(str(event.group_id), str(event.user_id))

# --- 数据I/O与全局数据字典 (供其他模块导入) ---

def load(file, waifu_reset):
    if not file.exists():
        logger.info(f"{file} 未找到，创建新记录。")
        return {}

    try:
        # 检查文件是否为今天
        file_mtime = os.path.getmtime(file)

        if waifu_reset and file_mtime <= Zero_today:
            # 'waifu_reset' 为 True，且文件是昨天的 -> 重置
            logger.info(f"{file} 是旧文件，已重置。")
            return {}
        else:
            # 'waifu_reset' 为 False -> 总是加载
            # 'waifu_reset' 为 True，且文件是今天的 -> 加载
            with open(file, 'r') as f:
                line = f.read()
                if not line:  # 处理空文件
                    logger.info(f"{file} 为空，返回空字典。")
                    return {}
                record = eval(line)
            logger.info(f"{file} 已加载")
            return record
    except Exception as e:
        logger.error(f"加载 {file} 失败: {e}。文件将重置。")
        return {}

if waifu_save:
    def save(file, data):
        with open(file, "w", encoding="utf8") as f:
            f.write(str(data))
else:
    def save(file, data):
        pass

waifu_file = Path() / "data" / "waifu"

if not waifu_file.exists():
    os.makedirs(waifu_file)

# 定义文件路径 (供其他模块导入)
record_CP_file = waifu_file / "record_CP"
record_waifu_file = waifu_file / "record_waifu"
record_lock_file = waifu_file / "record_lock"
record_yinpa1_file = waifu_file / "record_yinpa1"
record_yinpa2_file = waifu_file / "record_yinpa2"
protect_list_file = waifu_file / "list_protect"

# 定义全局数据字典 (供其他模块导入)
record_CP = load(record_CP_file, waifu_reset)
record_waifu = load(record_waifu_file, waifu_reset)
record_lock = load(record_lock_file, waifu_reset)
record_yinpa1 = load(record_yinpa1_file, waifu_reset)
record_yinpa2 = load(record_yinpa2_file, waifu_reset)

if protect_list_file.exists():
    with open(protect_list_file, 'r') as f:
        line = f.read()
        protect_list = eval(line)
else:
    protect_list = {}

# --- 定时任务 ---
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

if waifu_reset:
    def reset_record():
        global record_CP, record_waifu, record_lock, record_yinpa1, record_yinpa2
        record_CP = {}
        record_waifu = {}
        record_lock = {}
        record_yinpa1 = {}
        record_yinpa2 = {}

        # 【修复】增加保存操作
        save(record_CP_file, record_CP)
        save(record_waifu_file, record_waifu)
        save(record_lock_file, record_lock)
        save(record_yinpa1_file, record_yinpa1)
        save(record_yinpa2_file, record_yinpa2)

        logger.info(f"娶群友记录已重置 (waifu_reset=True)")
else:
    def reset_record():
        global record_CP, record_yinpa1, record_yinpa2

        # 【修复】修正重置单身记录的逻辑
        for group_id in record_CP:
            users_to_remove = []
            for user_id, waifu_id in record_CP[group_id].items():
                if user_id == waifu_id:
                    users_to_remove.append(user_id)

            for user_id in users_to_remove:
                del record_CP[group_id][user_id]

        record_yinpa1 = {}
        record_yinpa2 = {}

        # 【修复】增加保存操作
        save(record_CP_file, record_CP)
        save(record_yinpa1_file, record_yinpa1)
        save(record_yinpa2_file, record_yinpa2)

        logger.info(f"娶群友记录已重置 (waifu_reset=False)")

on_command("重置记录", priority=10, block=True).append_handler(reset_record)
scheduler.add_job(reset_record, "cron", hour=0, misfire_grace_time=120)


# --- 导入子模块，加载 Matcher ---
from . import marry
from . import yinpa