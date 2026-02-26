import json
import os
from datetime import datetime
from typing import Dict, List, Set

from .config import STATS_FILE

# 导入管理模块
from ..plugin_manager.enable import is_plugin_enabled
from ..utils.tools import get_logger

logger = get_logger("group_statistics.data_manager")


class GroupStatisticsData:
    def __init__(self):
        self.group_stats: Dict[int, Dict[str, int]] = {}  # {group_id: {user_id: count}}
        self.user_info: Dict[int, Dict[str, str]] = {}  # {group_id: {user_id: card}}
        self.load_data()

    def load_data(self):
        """从文件加载数据"""
        # 加载统计数据
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.group_stats = {int(k): v for k, v in data.get('group_stats', {}).items()}
                    self.user_info = {int(k): v for k, v in data.get('user_info', {}).items()}
            except Exception as e:
                logger.exception(f"加载统计数据失败: {e}")

    def save_stats(self):
        """保存统计数据到文件"""
        try:
            data = {
                'group_stats': self.group_stats,
                'user_info': self.user_info
            }
            with open(STATS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception(f"保存统计数据失败: {e}")

    def record_user_message(self, group_id: int, user_id: int, user_card: str):
        """记录用户消息"""
        # 检查插件是否启用
        if not is_plugin_enabled("group_statistics", str(group_id), "0"):
            return

        # 初始化群组数据
        if group_id not in self.group_stats:
            self.group_stats[group_id] = {}
        if group_id not in self.user_info:
            self.user_info[group_id] = {}

        # 更新消息计数
        user_id_str = str(user_id)
        if user_id_str in self.group_stats[group_id]:
            self.group_stats[group_id][user_id_str] += 1
        else:
            self.group_stats[group_id][user_id_str] = 1

        # 更新用户信息（群名片）
        self.user_info[group_id][user_id_str] = user_card

        # 保存数据
        self.save_stats()


# 全局数据实例
data_manager = GroupStatisticsData()
