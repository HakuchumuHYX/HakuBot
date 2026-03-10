"""HLTV 订阅数据管理"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from nonebot_plugin_localstore import get_plugin_data_dir

from ..utils.tools import get_logger

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
    """群组数据（仅保留群维度开关，订阅走全局 canonical）"""

    group_id: int
    enabled: bool = False  # 是否启用插件，默认禁用，需要管理员手动开启
    # 兼容历史数据：仍保留字段用于迁移读取，不再作为真实数据源
    subscribed_events: list[EventSubscription] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "enabled": self.enabled,
            "subscribed_events": [asdict(e) for e in self.subscribed_events],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroupData":
        events = [EventSubscription(**e) for e in data.get("subscribed_events", [])]
        return cls(
            group_id=data["group_id"],
            # 默认关闭：只有显式开启才启用（缺字段的历史数据也应默认关闭）
            enabled=data.get("enabled", False),
            subscribed_events=events,
        )


class DataManager:
    """数据管理器"""

    def __init__(self):
        self._data_dir: Path = get_plugin_data_dir()
        self._data_file: Path = self._data_dir / "subscriptions.json"
        self._groups: dict[int, GroupData] = {}

        # 全局 canonical 订阅集合（全局同步语义）
        self._global_subscriptions: list[EventSubscription] = []

        # 定时推送状态（带时间戳，支持 TTL 清理）
        self._notified_starts: dict[str, str] = {}
        self._notified_results: dict[str, str] = {}

        self._load()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()

    @staticmethod
    def _normalize_notified(raw: object) -> dict[str, str]:
        """兼容旧格式(list[str])和新格式(dict[str, iso_datetime])"""
        if isinstance(raw, dict):
            out: dict[str, str] = {}
            for k, v in raw.items():
                if isinstance(k, str):
                    out[k] = v if isinstance(v, str) else datetime.now().isoformat()
            return out

        if isinstance(raw, list):
            now = datetime.now().isoformat()
            return {str(match_id): now for match_id in raw}

        return {}

    def _load(self) -> None:
        """加载数据"""
        if not self._data_file.exists():
            return

        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            dirty = False

            # 1) groups
            for group_data in data.get("groups", []):
                if "enabled" not in group_data:
                    group_data["enabled"] = False
                    dirty = True

                gd = GroupData.from_dict(group_data)
                self._groups[gd.group_id] = gd

            # 2) 全局订阅迁移
            if "global_subscriptions" in data:
                self._global_subscriptions = [
                    EventSubscription(**e) for e in data.get("global_subscriptions", [])
                ]
            else:
                # 从“启用群”的历史 subscribed_events 合并去重得到 canonical
                merged: dict[str, EventSubscription] = {}
                for group in self._groups.values():
                    if not group.enabled:
                        continue
                    for sub in group.subscribed_events:
                        merged[sub.event_id] = sub
                self._global_subscriptions = list(merged.values())
                dirty = True

            # 3) 去重状态迁移
            scheduler_state = data.get("scheduler_state", {})
            self._notified_starts = self._normalize_notified(
                scheduler_state.get("notified_starts", {})
            )
            self._notified_results = self._normalize_notified(
                scheduler_state.get("notified_results", {})
            )

            # 4) legacy group.subscribed_events 与 canonical 对齐（便于排查）
            canonical = [asdict(e) for e in self._global_subscriptions]
            for group in self._groups.values():
                if [asdict(e) for e in group.subscribed_events] != canonical:
                    group.subscribed_events = [EventSubscription(**e) for e in canonical]
                    dirty = True

            if dirty:
                self._save()

        except Exception as e:
            logger.exception(f"[HLTV Sub] 加载数据失败: {e}")

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
                },
            }
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception(f"[HLTV Sub] 保存数据失败: {e}")

    def _sync_legacy_group_subscriptions(self) -> None:
        """把 canonical 同步回每个群，保持文件结构兼容且便于人工查看"""
        canonical = [EventSubscription(**asdict(e)) for e in self._global_subscriptions]
        for group in self._groups.values():
            group.subscribed_events = [EventSubscription(**asdict(e)) for e in canonical]

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
        """获取群组订阅的赛事列表（全局同步语义）"""
        _ = self.get_group(group_id)  # 确保群对象存在
        return [EventSubscription(**asdict(e)) for e in self._global_subscriptions]

    def get_single_subscription(self, group_id: int) -> Optional[EventSubscription]:
        """获取群组的第一条订阅，不存在返回 None"""
        subs = self.get_subscribed_events(group_id)
        return subs[0] if subs else None

    def subscribe_event(self, group_id: int, subscription: EventSubscription) -> bool:
        """新增赛事订阅（全局同步），返回是否新增成功"""
        _ = self.get_group(group_id)  # 确保发起操作的群有记录

        if any(s.event_id == subscription.event_id for s in self._global_subscriptions):
            return False

        self._global_subscriptions.append(subscription)
        self._sync_legacy_group_subscriptions()
        self._save()
        return True

    def clear_subscriptions(self, group_id: int) -> None:
        """清空全局订阅列表（保留旧接口语义）"""
        _ = self.get_group(group_id)
        self._global_subscriptions = []
        self._sync_legacy_group_subscriptions()
        self._save()

    def get_subscribed_event_ids(self, group_id: int) -> list[str]:
        """获取群组订阅的赛事ID列表"""
        return [e.event_id for e in self.get_subscribed_events(group_id)]

    def unsubscribe_event(self, group_id: int, event_id: str) -> bool:
        """取消订阅赛事（全局同步），返回是否成功"""
        _ = self.get_group(group_id)
        return self.unsubscribe_event_global(event_id)

    def unsubscribe_event_global(self, event_id: str) -> bool:
        """取消订阅赛事（全局，不依赖群）"""
        before = len(self._global_subscriptions)
        self._global_subscriptions = [e for e in self._global_subscriptions if e.event_id != event_id]
        removed = len(self._global_subscriptions) != before

        if removed:
            self._sync_legacy_group_subscriptions()
            self._save()

        return removed

    def is_subscribed(self, group_id: int, event_id: str) -> bool:
        """检查是否已订阅赛事"""
        return event_id in self.get_subscribed_event_ids(group_id)

    def get_all_subscribed_event_ids(self) -> set[str]:
        """获取当前全局订阅赛事ID（用于定时任务）"""
        return {event.event_id for event in self._global_subscriptions}

    def get_groups_by_event(self, event_id: str) -> list[int]:
        """获取订阅了某赛事的群组列表（仅启用群）"""
        if event_id not in self.get_all_subscribed_event_ids():
            return []

        groups = []
        for group in self._groups.values():
            if group.enabled:
                groups.append(group.group_id)
        return groups

    def get_any_subscription_by_event(self, event_id: str) -> Optional[EventSubscription]:
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
            self._sync_legacy_group_subscriptions()
            self._save()
        return changed

    # -------------------- 推送去重状态 --------------------

    def get_notified_starts(self) -> set[str]:
        """获取已发送开始提醒的比赛ID集合"""
        return set(self._notified_starts.keys())

    def add_notified_start(self, match_id: str) -> None:
        """添加已发送开始提醒的比赛ID"""
        self._notified_starts[match_id] = self._now_iso()
        self._save()

    def get_notified_results(self) -> set[str]:
        """获取已发送结果的比赛ID集合"""
        return set(self._notified_results.keys())

    def add_notified_result(self, match_id: str) -> None:
        """添加已发送结果的比赛ID"""
        self._notified_results[match_id] = self._now_iso()
        self._save()

    def is_start_notified(self, match_id: str) -> bool:
        """检查比赛开始提醒是否已发送"""
        return match_id in self._notified_starts

    def is_result_notified(self, match_id: str) -> bool:
        """检查比赛结果是否已推送"""
        return match_id in self._notified_results

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

        if removed_starts or removed_results:
            self._save()

        return removed_starts, removed_results


# 全局数据管理器实例
data_manager = DataManager()
