"""HLTV 订阅数据管理"""

import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict
from nonebot_plugin_localstore import get_plugin_data_dir


@dataclass
class EventSubscription:
    """赛事订阅信息"""
    event_id: str
    event_title: str
    start_date: str = ""
    end_date: str = ""


@dataclass
class GroupData:
    """群组数据"""
    group_id: int
    enabled: bool = False  # 是否启用插件，默认禁用，需要管理员手动开启
    subscribed_events: list[EventSubscription] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "enabled": self.enabled,
            "subscribed_events": [asdict(e) for e in self.subscribed_events]
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "GroupData":
        events = [EventSubscription(**e) for e in data.get("subscribed_events", [])]
        return cls(
            group_id=data["group_id"],
            enabled=data.get("enabled", True),
            subscribed_events=events
        )


class DataManager:
    """数据管理器"""
    
    def __init__(self):
        self._data_dir: Path = get_plugin_data_dir()
        self._data_file: Path = self._data_dir / "subscriptions.json"
        self._groups: dict[int, GroupData] = {}
        # 定时推送状态
        self._notified_starts: set[str] = set()  # 已发送开始提醒的比赛ID
        self._notified_results: set[str] = set()  # 已发送结果的比赛ID
        self._load()
    
    def _load(self) -> None:
        """加载数据"""
        if self._data_file.exists():
            try:
                with open(self._data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for group_data in data.get("groups", []):
                        gd = GroupData.from_dict(group_data)
                        self._groups[gd.group_id] = gd
                    # 加载推送状态
                    scheduler_state = data.get("scheduler_state", {})
                    self._notified_starts = set(scheduler_state.get("notified_starts", []))
                    self._notified_results = set(scheduler_state.get("notified_results", []))
            except Exception as e:
                print(f"[HLTV Sub] 加载数据失败: {e}")
    
    def _save(self) -> None:
        """保存数据"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "groups": [g.to_dict() for g in self._groups.values()],
                "scheduler_state": {
                    "notified_starts": list(self._notified_starts),
                    "notified_results": list(self._notified_results)
                }
            }
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[HLTV Sub] 保存数据失败: {e}")
    
    def get_group(self, group_id: int) -> GroupData:
        """获取群组数据，不存在则创建"""
        if group_id not in self._groups:
            self._groups[group_id] = GroupData(group_id=group_id)
        return self._groups[group_id]
    
    def is_enabled(self, group_id: int) -> bool:
        """检查群组是否启用插件"""
        return self.get_group(group_id).enabled
    
    def set_enabled(self, group_id: int, enabled: bool) -> None:
        """设置群组启用状态"""
        self.get_group(group_id).enabled = enabled
        self._save()
    
    def get_subscribed_events(self, group_id: int) -> list[EventSubscription]:
        """获取群组订阅的赛事列表"""
        return self.get_group(group_id).subscribed_events
    
    def get_subscribed_event_ids(self, group_id: int) -> list[str]:
        """获取群组订阅的赛事ID列表"""
        return [e.event_id for e in self.get_subscribed_events(group_id)]
    
    def subscribe_event(self, group_id: int, event_id: str, event_title: str, 
                       start_date: str = "", end_date: str = "") -> bool:
        """订阅赛事，返回是否成功（已订阅返回 False）"""
        group = self.get_group(group_id)
        
        # 检查是否已订阅
        for event in group.subscribed_events:
            if event.event_id == event_id:
                return False
        
        # 添加订阅
        group.subscribed_events.append(EventSubscription(
            event_id=event_id,
            event_title=event_title,
            start_date=start_date,
            end_date=end_date
        ))
        self._save()
        return True
    
    def unsubscribe_event(self, group_id: int, event_id: str) -> bool:
        """取消订阅赛事，返回是否成功（未订阅返回 False）"""
        group = self.get_group(group_id)
        
        for i, event in enumerate(group.subscribed_events):
            if event.event_id == event_id:
                group.subscribed_events.pop(i)
                self._save()
                return True
        
        return False
    
    def is_subscribed(self, group_id: int, event_id: str) -> bool:
        """检查是否已订阅赛事"""
        return event_id in self.get_subscribed_event_ids(group_id)
    
    def get_all_subscribed_event_ids(self) -> set[str]:
        """获取所有群组订阅的赛事ID（用于定时任务）"""
        event_ids = set()
        for group in self._groups.values():
            if group.enabled:
                for event in group.subscribed_events:
                    event_ids.add(event.event_id)
        return event_ids
    
    def get_groups_by_event(self, event_id: str) -> list[int]:
        """获取订阅了某赛事的群组列表"""
        groups = []
        for group in self._groups.values():
            if group.enabled:
                for event in group.subscribed_events:
                    if event.event_id == event_id:
                        groups.append(group.group_id)
                        break
        return groups


    def get_notified_starts(self) -> set[str]:
        """获取已发送开始提醒的比赛ID集合"""
        return self._notified_starts.copy()
    
    def add_notified_start(self, match_id: str) -> None:
        """添加已发送开始提醒的比赛ID"""
        self._notified_starts.add(match_id)
        self._save()
    
    def get_notified_results(self) -> set[str]:
        """获取已发送结果的比赛ID集合"""
        return self._notified_results.copy()
    
    def add_notified_result(self, match_id: str) -> None:
        """添加已发送结果的比赛ID"""
        self._notified_results.add(match_id)
        self._save()
    
    def is_start_notified(self, match_id: str) -> bool:
        """检查比赛开始提醒是否已发送"""
        return match_id in self._notified_starts
    
    def is_result_notified(self, match_id: str) -> bool:
        """检查比赛结果是否已推送"""
        return match_id in self._notified_results
    
    def clean_old_notifications(self, valid_match_ids: set[str]) -> None:
        """清理过期的通知记录（保留仍有效的比赛ID）"""
        self._notified_starts = self._notified_starts & valid_match_ids
        self._notified_results = self._notified_results & valid_match_ids
        self._save()


# 全局数据管理器实例
data_manager = DataManager()
