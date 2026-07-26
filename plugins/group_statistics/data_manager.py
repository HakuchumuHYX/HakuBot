import json
import os
from datetime import datetime
from typing import Dict, List, Set

from .config import STATS_FILE

from ..utils.json_io import atomic_write_json
from ..utils.tools import get_logger

logger = get_logger("group_statistics.data_manager")


class GroupStatisticsData:
    def __init__(self):
        self.group_stats: Dict[int, Dict[str, int]] = {}  # {group_id: {user_id: count}}
        self.user_info: Dict[int, Dict[str, str]] = {}  # {group_id: {user_id: card}}
        self._dirty = False  # 是否有未落盘的改动
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
            # 先做快照，避免写盘（可能在线程中执行）时事件循环并发修改字典
            data = {
                'group_stats': {gid: dict(users) for gid, users in self.group_stats.items()},
                'user_info': {gid: dict(info) for gid, info in self.user_info.items()}
            }
            atomic_write_json(STATS_FILE, data)
        except Exception as e:
            logger.exception(f"保存统计数据失败: {e}")

    def flush(self):
        """若有未落盘的改动则保存并清除脏标记"""
        if self._dirty:
            self.save_stats()
            self._dirty = False

    def record_user_message(self, group_id: int, user_id: int, user_card: str):
        """记录用户消息（插件是否启用由调用方检查）"""
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

        # 标记为待落盘，由定时任务统一保存
        self._dirty = True


# 全局数据实例
data_manager = GroupStatisticsData()
