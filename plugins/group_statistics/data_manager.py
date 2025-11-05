import json
import os
from datetime import datetime
from typing import Dict, List, Set
from .config import STATS_FILE

# 导入管理模块
from ..plugin_manager.enable import is_plugin_enabled


class GroupStatisticsData:
    def __init__(self):
        self.group_stats: Dict[int, Dict[str, int]] = {}  # {group_id: {user_id: count}}
        self.user_info: Dict[int, Dict[str, str]] = {}  # {group_id: {user_id: card}}
        self.bot_self_id: str = ""  # 机器人自身ID
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
                    self.bot_self_id = data.get('bot_self_id', "")
            except Exception as e:
                print(f"加载统计数据失败: {e}")

    def save_stats(self):
        """保存统计数据到文件"""
        try:
            data = {
                'group_stats': self.group_stats,
                'user_info': self.user_info,
                'bot_self_id': self.bot_self_id
            }
            with open(STATS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存统计数据失败: {e}")

    def set_bot_self_id(self, self_id: str):
        """设置机器人自身ID"""
        self.bot_self_id = self_id
        self.save_stats()

    def record_bot_message(self, group_id: int, bot_card: str = "机器人"):
        """记录机器人发送的消息"""
        # 检查插件是否启用
        if not is_plugin_enabled("group_statistics", str(group_id)):
            return

        # 初始化群组数据
        if group_id not in self.group_stats:
            self.group_stats[group_id] = {}
        if group_id not in self.user_info:
            self.user_info[group_id] = {}

        # 更新机器人消息计数
        if self.bot_self_id:
            if self.bot_self_id in self.group_stats[group_id]:
                self.group_stats[group_id][self.bot_self_id] += 1
            else:
                self.group_stats[group_id][self.bot_self_id] = 1

            # 更新机器人名片
            self.user_info[group_id][self.bot_self_id] = bot_card

            # 保存数据
            self.save_stats()

    def record_user_message(self, group_id: int, user_id: int, user_card: str):
        """记录用户消息"""
        # 检查插件是否启用
        if not is_plugin_enabled("group_statistics", str(group_id)):
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