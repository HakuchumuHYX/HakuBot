"""HLTV 订阅数据管理"""

from __future__ import annotations
from utils.paths import PluginPaths

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from utils.json_io import atomic_write_json
from typing import Optional


from utils.logging import get_logger

logger = get_logger("hltv_sub.data_manager")


@dataclass
class EventSubscription:
    """赛事订阅信息"""

    event_id: str
    event_title: str
    start_date: str = ""
    end_date: str = ""


@dataclass
class GroupData:
    """群组推送开关；赛事订阅由全局集合保存。"""

    group_id: int
    enabled: bool = False  # 是否启用插件，默认禁用，需要管理员手动开启

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroupData":
        return cls(
            group_id=data["group_id"],
            # 默认关闭：只有显式开启才启用（缺字段的历史数据也应默认关闭）
            enabled=data.get("enabled", False),
        )


class DataManager:
    """数据管理器"""

    def __init__(self):
        self._data_dir: Path = PluginPaths("hltv_sub").data
        self._data_file: Path = self._data_dir / "subscriptions.json"
        self._backup_file: Path = self._data_dir / "subscriptions.json.bak"
        self._groups: dict[int, GroupData] = {}

        # 全局 canonical 订阅集合（全局同步语义）
        self._global_subscriptions: list[EventSubscription] = []

        # 定时推送状态（带时间戳，支持 TTL 清理）
        self._notified_starts: dict[str, str] = {}
        self._notified_results: dict[str, str] = {}
        self._notified_map_results: dict[str, str] = {}

        # 去重状态写盘节流（降低高频轮询下磁盘写放大）
        self._notified_dirty: bool = False
        self._notified_pending_ops: int = 0
        self._last_notified_save_at: Optional[datetime] = None
        self._notified_save_interval_seconds: int = 30
        self._notified_save_max_ops: int = 20

        self._load()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()

    def _load(self) -> None:
        """加载数据"""
        if not self._data_file.exists():
            return

        data: dict
        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.exception(f"[HLTV Sub] 加载主数据失败: {e}")
            if not self._backup_file.exists():
                return
            try:
                with open(self._backup_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("[HLTV Sub] 已从备份文件恢复订阅数据")
            except Exception as backup_error:
                logger.exception(f"[HLTV Sub] 加载备份数据失败: {backup_error}")
                return

        try:
            self._groups = {
                group.group_id: group
                for group in (GroupData.from_dict(item) for item in data.get("groups", []))
            }
            self._global_subscriptions = [
                EventSubscription(**item)
                for item in data.get("global_subscriptions", [])
            ]
            scheduler_state = data.get("scheduler_state", {})
            self._notified_starts = scheduler_state.get("notified_starts", {})
            self._notified_results = scheduler_state.get("notified_results", {})
            self._notified_map_results = scheduler_state.get("notified_map_results", {})

        except Exception as e:
            logger.exception(f"[HLTV Sub] 解析数据失败: {e}")

    def _save(self) -> None:
        """保存数据"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "groups": [g.to_dict() for g in self._groups.values()],
                "global_subscriptions": [asdict(e) for e in self._global_subscriptions],
                "scheduler_state": {
                    "notified_starts": self._notified_starts,
                    "notified_results": self._notified_results,
                    "notified_map_results": self._notified_map_results,
                },
            }
            if self._data_file.exists():
                shutil.copyfile(self._data_file, self._backup_file)
            atomic_write_json(self._data_file, data)

            # 任意全量保存后，清空去重状态节流计数
            self._notified_dirty = False
            self._notified_pending_ops = 0
            self._last_notified_save_at = datetime.now()
        except Exception as e:
            logger.exception(f"[HLTV Sub] 保存数据失败: {e}")

    def _save_notified_state_debounced(self, force: bool = False) -> None:
        """去重状态写盘节流：在高频 add_notified_* 下按时间/次数批量落盘"""
        self._notified_dirty = True
        self._notified_pending_ops += 1

        now = datetime.now()
        if self._last_notified_save_at is None:
            self._last_notified_save_at = now

        elapsed = (now - self._last_notified_save_at).total_seconds()
        should_flush = (
            force
            or self._notified_pending_ops >= self._notified_save_max_ops
            or elapsed >= self._notified_save_interval_seconds
        )

        if should_flush:
            self._save()

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

    def get_subscribed_events(self) -> list[EventSubscription]:
        """获取全局赛事订阅列表。"""
        return [EventSubscription(**asdict(e)) for e in self._global_subscriptions]

    def subscribe_event(self, subscription: EventSubscription) -> bool:
        """新增赛事订阅（全局同步），返回是否新增成功"""
        if self.is_subscribed(subscription.event_id):
            return False

        self._global_subscriptions.append(subscription)
        self._save()
        return True

    def unsubscribe_event(self, event_id: str) -> bool:
        """取消订阅赛事（全局，不依赖群）"""
        before = len(self._global_subscriptions)
        self._global_subscriptions = [
            e for e in self._global_subscriptions if e.event_id != event_id
        ]
        removed = len(self._global_subscriptions) != before

        if removed:
            self._save()

        return removed

    def is_subscribed(self, event_id: str) -> bool:
        """检查全局是否已订阅赛事。"""
        return any(event.event_id == event_id for event in self._global_subscriptions)

    def get_all_subscribed_event_ids(self) -> set[str]:
        """获取当前全局订阅赛事ID（用于定时任务）"""
        return {event.event_id for event in self._global_subscriptions}

    def get_groups_by_event(self, event_id: str) -> list[int]:
        """获取订阅了某赛事的群组列表（仅启用群）"""
        if not self.is_subscribed(event_id):
            return []

        groups = []
        for group in self._groups.values():
            if group.enabled:
                groups.append(group.group_id)
        return groups

    def get_any_subscription_by_event(
        self, event_id: str
    ) -> Optional[EventSubscription]:
        """从全局订阅中读取某赛事元信息"""
        for event in self._global_subscriptions:
            if event.event_id == event_id:
                return EventSubscription(**asdict(event))
        return None

    def update_subscription_meta(
        self,
        event_id: str,
        *,
        event_title: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        """更新某赛事订阅元信息，返回是否发生更新"""
        changed = False
        for event in self._global_subscriptions:
            if event.event_id != event_id:
                continue
            if event_title is not None and event_title != event.event_title:
                event.event_title = event_title
                changed = True
            if start_date is not None and start_date != event.start_date:
                event.start_date = start_date
                changed = True
            if end_date is not None and end_date != event.end_date:
                event.end_date = end_date
                changed = True

        if changed:
            self._save()
        return changed

    # -------------------- 推送去重状态 --------------------

    def add_notified_start(self, match_id: str, *, force: bool = False) -> None:
        """添加已发送开始提醒的比赛ID"""
        self._notified_starts[match_id] = self._now_iso()
        self._save_notified_state_debounced(force=force)

    def add_notified_result(self, match_id: str, *, force: bool = False) -> None:
        """添加已发送结果的比赛ID"""
        self._notified_results[match_id] = self._now_iso()
        self._save_notified_state_debounced(force=force)

    def add_notified_map_result(
        self, notification_id: str, *, force: bool = False
    ) -> None:
        """添加已发送单图结果的去重键"""
        self._notified_map_results[notification_id] = self._now_iso()
        self._save_notified_state_debounced(force=force)

    def is_start_notified(self, match_id: str) -> bool:
        """检查比赛开始提醒是否已发送"""
        return match_id in self._notified_starts

    def is_result_notified(self, match_id: str) -> bool:
        """检查比赛结果是否已推送"""
        return match_id in self._notified_results

    def is_map_result_notified(self, notification_id: str) -> bool:
        """检查单图结果是否已推送"""
        return notification_id in self._notified_map_results

    def cleanup_notified_state(self, ttl_days: int = 30) -> tuple[int, int]:
        """清理过期去重状态，返回 (清理 starts 数, 清理 results 数)"""
        cutoff = datetime.now() - timedelta(days=max(1, int(ttl_days)))

        def _cleanup(raw: dict[str, str]) -> int:
            before = len(raw)
            kept: dict[str, str] = {}
            for match_id, ts in raw.items():
                try:
                    dt = datetime.fromisoformat(ts)
                except Exception:
                    dt = datetime.now()
                if dt >= cutoff:
                    kept[match_id] = ts
            raw.clear()
            raw.update(kept)
            return before - len(raw)

        removed_starts = _cleanup(self._notified_starts)
        removed_results = _cleanup(self._notified_results)
        removed_map_results = _cleanup(self._notified_map_results)

        if removed_starts or removed_results or removed_map_results:
            self._save_notified_state_debounced(force=True)

        return removed_starts, removed_results


# 全局数据管理器实例
data_manager = DataManager()
