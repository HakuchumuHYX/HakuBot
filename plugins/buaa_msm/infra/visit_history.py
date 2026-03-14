# plugins/buaa_msm/infra/visit_history.py
"""
访问历史（infra）

职责：
- 记录“每次上传文件中的来访角色”
- 提供“与本日其他时段重复角色”的计算（供渲染层高亮使用）
- 管理持久化文件（json）

说明：
- 原实现来自 `plugins/buaa_msm/data_manage.py`，为拆分职责迁移至此。
- 不应包含 NoneBot 命令/定时任务注册（这些应在 handlers）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from nonebot.log import logger

from ..config import plugin_config


visit_history_file = plugin_config.visit_history_file


class VisitHistoryManager:
    """访问历史管理器（带内存缓存）"""

    def __init__(self):
        self._cache: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._dirty = False

    def _load(self) -> Dict[str, List[Dict[str, Any]]]:
        """从文件加载访问历史"""
        if not visit_history_file.exists():
            return {}
        try:
            return json.loads(visit_history_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"加载访问历史失败: {e}")
            return {}

    def _save(self, data: Dict[str, List[Dict[str, Any]]]):
        """保存访问历史到文件"""
        try:
            visit_history_file.parent.mkdir(parents=True, exist_ok=True)
            visit_history_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存访问历史失败: {e}")

    def get_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取访问历史数据（优先使用缓存）"""
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def save_if_dirty(self):
        """如果有修改则保存"""
        if self._dirty and self._cache is not None:
            self._save(self._cache)
            self._dirty = False

    def get_current_period_key(self) -> str:
        """获取当前时间的时段Key"""
        now = datetime.now()
        morning_start = plugin_config.time_config.morning_start
        afternoon_start = plugin_config.time_config.afternoon_start

        if now.hour < morning_start:
            date_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            part = "afternoon"
        elif morning_start <= now.hour < afternoon_start:
            date_str = now.strftime("%Y-%m-%d")
            part = "morning"
        else:
            date_str = now.strftime("%Y-%m-%d")
            part = "afternoon"

        return f"{date_str}_{part}"

    def record_visit(self, user_id: str, character_group_ids: List[str]):
        """记录用户本次上传文件中的来访角色"""
        data = self.get_data()
        if user_id not in data:
            data[user_id] = []

        current_period = self.get_current_period_key()
        new_record = {
            "period_key": current_period,
            "timestamp": time.time(),
            "characters": character_group_ids,
        }

        period_found = False
        for i, record in enumerate(data[user_id]):
            if record.get("period_key") == current_period:
                data[user_id][i] = new_record
                period_found = True
                logger.info(f"覆盖用户 {user_id} 在 {current_period} 时段的访问记录")
                break

        if not period_found:
            data[user_id].append(new_record)
            logger.info(f"新增用户 {user_id} 在 {current_period} 时段的访问记录")

        self._dirty = True
        self.save_if_dirty()

    def get_duplicate_chars_for_latest(self, user_id: str) -> Set[str]:
        """获取最新一次上传中，与本日其他时段记录重复的角色ID"""
        data = self.get_data()
        records = data.get(user_id, [])

        if not records:
            return set()

        current_period = self.get_current_period_key()

        latest_chars = set()
        for record in records:
            if record.get("period_key") == current_period:
                latest_chars = set(record.get("characters") or [])
                break

        if not latest_chars:
            return set()

        previous_chars = set()
        for record in records:
            if record.get("period_key") != current_period:
                previous_chars.update(record.get("characters") or [])

        return latest_chars.intersection(previous_chars)

    def clear(self):
        """清空所有访问历史"""
        try:
            visit_history_file.parent.mkdir(parents=True, exist_ok=True)
            visit_history_file.write_text("{}", encoding="utf-8")
            self._cache = {}
            self._dirty = False
            logger.success("已清空角色访问历史")
        except Exception as e:
            logger.error(f"清空访问历史失败: {e}")


# 全局访问历史管理器实例
visit_history_manager = VisitHistoryManager()


# 便捷函数（保持向后兼容语义）
def record_character_visit(user_id: str, character_group_ids: List[str]):
    visit_history_manager.record_visit(user_id, character_group_ids)


def get_duplicate_chars_for_latest(user_id: str) -> Set[str]:
    return visit_history_manager.get_duplicate_chars_for_latest(user_id)
