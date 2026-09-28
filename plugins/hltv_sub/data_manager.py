"""HLTV 全局订阅、群开关和推送去重。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Optional

from utils.json_io import atomic_write_json
from utils.paths import PluginPaths


@dataclass
class EventSubscription:
    event_id: str
    event_title: str
    start_date: str
    end_date: str


@dataclass
class GroupData:
    group_id: int
    enabled: bool = False


class DataManager:
    def __init__(self):
        self._data_file = PluginPaths("hltv_sub").data / "subscriptions.json"
        self._data = (
            json.loads(self._data_file.read_text(encoding="utf-8"))
            if self._data_file.exists() else {}
        )
        self._groups = {
            item["group_id"]: GroupData(item["group_id"], item["enabled"])
            for item in self._data.get("groups", [])
        }
        self._global_subscriptions = [
            EventSubscription(
                item["event_id"], item["event_title"],
                item["start_date"], item["end_date"],
            )
            for item in self._data.get("global_subscriptions", [])
        ]
        state = self._data.get("scheduler_state", {})
        self._notified_starts = state.get("notified_starts", {})
        self._notified_results = state.get("notified_results", {})
        self._notified_map_results = state.get("notified_map_results", {})

    def _save(
        self, *, groups=None, subscriptions=None, notified_starts=None,
        notified_results=None, notified_map_results=None,
    ) -> None:
        groups = self._groups if groups is None else groups
        subscriptions = self._global_subscriptions if subscriptions is None else subscriptions
        starts = self._notified_starts if notified_starts is None else notified_starts
        results = self._notified_results if notified_results is None else notified_results
        maps = self._notified_map_results if notified_map_results is None else notified_map_results
        old_groups = {g["group_id"]: g for g in self._data.get("groups", [])}
        old_subs = {s["event_id"]: s for s in self._data.get("global_subscriptions", [])}
        data = {
            **self._data,
            "groups": [
                {**old_groups.get(g.group_id, {}), **asdict(g)}
                for g in groups.values()
            ],
            "global_subscriptions": [
                {**old_subs.get(s.event_id, {}), **asdict(s)}
                for s in subscriptions
            ],
            "scheduler_state": {
                **self._data.get("scheduler_state", {}),
                "notified_starts": starts,
                "notified_results": results,
                "notified_map_results": maps,
            },
        }
        # 只有写入成功才发布内存状态；失败交给调用方报告。
        atomic_write_json(self._data_file, data)
        self._data = data
        self._groups = groups
        self._global_subscriptions = subscriptions
        self._notified_starts = starts
        self._notified_results = results
        self._notified_map_results = maps

    def is_enabled(self, group_id: int) -> bool:
        group = self._groups.get(group_id)
        return group is not None and group.enabled

    def has_enabled_groups(self) -> bool:
        return any(group.enabled for group in self._groups.values())

    def set_enabled(self, group_id: int, enabled: bool) -> None:
        self._save(groups={**self._groups, group_id: GroupData(group_id, enabled)})

    def get_subscribed_events(self) -> list[EventSubscription]:
        return list(self._global_subscriptions)

    def subscribe_event(self, subscription: EventSubscription) -> bool:
        if self.is_subscribed(subscription.event_id):
            return False
        self._save(subscriptions=[*self._global_subscriptions, subscription])
        return True

    def unsubscribe_event(self, event_id: str) -> bool:
        remaining = [s for s in self._global_subscriptions if s.event_id != event_id]
        if len(remaining) == len(self._global_subscriptions):
            return False
        self._save(subscriptions=remaining)
        return True

    def is_subscribed(self, event_id: str) -> bool:
        return any(s.event_id == event_id for s in self._global_subscriptions)

    def get_all_subscribed_event_ids(self) -> set[str]:
        return {s.event_id for s in self._global_subscriptions}

    def get_groups_by_event(self, event_id: str) -> list[int]:
        if not self.is_subscribed(event_id):
            return []
        return [g.group_id for g in self._groups.values() if g.enabled]

    def get_subscription(self, event_id: str) -> Optional[EventSubscription]:
        return next((s for s in self._global_subscriptions if s.event_id == event_id), None)

    def update_subscription_meta(
        self, event_id: str, *, event_title: str,
        start_date: str, end_date: str,
    ) -> bool:
        current = self.get_subscription(event_id)
        if current is None:
            return False
        updated = replace(
            current, event_title=event_title,
            start_date=start_date, end_date=end_date,
        )
        if updated == current:
            return False
        self._save(subscriptions=[
            updated if s.event_id == event_id else s
            for s in self._global_subscriptions
        ])
        return True

    def add_notified_start(self, match_id: str) -> None:
        self._save(notified_starts={
            **self._notified_starts, match_id: datetime.now().isoformat(),
        })

    def add_notified_result(self, match_id: str) -> None:
        self._save(notified_results={
            **self._notified_results, match_id: datetime.now().isoformat(),
        })

    def add_notified_map_result(self, notification_id: str) -> None:
        self._save(notified_map_results={
            **self._notified_map_results, notification_id: datetime.now().isoformat(),
        })

    def is_start_notified(self, match_id: str) -> bool:
        return match_id in self._notified_starts

    def is_result_notified(self, match_id: str) -> bool:
        return match_id in self._notified_results

    def is_map_result_notified(self, notification_id: str) -> bool:
        return notification_id in self._notified_map_results

    def cleanup_notified_state(self, ttl_days: int = 30) -> int:
        cutoff = datetime.now() - timedelta(days=max(1, ttl_days))
        kept = [
            {key: ts for key, ts in records.items() if datetime.fromisoformat(ts) >= cutoff}
            for records in (
                self._notified_starts, self._notified_results, self._notified_map_results,
            )
        ]
        removed = (
            len(self._notified_starts) + len(self._notified_results)
            + len(self._notified_map_results) - sum(map(len, kept))
        )
        if removed:
            self._save(
                notified_starts=kept[0], notified_results=kept[1],
                notified_map_results=kept[2],
            )
        return removed


data_manager = DataManager()
