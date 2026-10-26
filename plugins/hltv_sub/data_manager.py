"""HLTV 全局订阅、群开关和推送去重。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from utils.json_io import atomic_write_json, load_json
from utils.paths import PluginPaths


@dataclass
class EventSubscription:
    event_id: str
    event_title: str
    start_date: str
    end_date: str
    # 决赛结果推送后置为 watching 开始扫 MVP，推送后为 pushed，超时为 expired。
    mvp_status: str = ""
    mvp_watch_since: str = ""


@dataclass
class GroupData:
    group_id: int
    enabled: bool = False


NotifiedKind = Literal["starts", "results", "map_results"]
NOTIFIED_KINDS: tuple[NotifiedKind, ...] = ("starts", "results", "map_results")


class DataManager:
    def __init__(self):
        self._data_file = PluginPaths("hltv_sub").data / "subscriptions.json"
        state = load_json(self._data_file, missing_ok=True, default={})
        if not state.success:
            raise ValueError("HLTV 订阅数据无法读取") from state.error
        self._groups = {
            item["group_id"]: GroupData(**item) for item in state.data.get("groups", [])
        }
        self._global_subscriptions = [
            EventSubscription(**item)
            for item in state.data.get("global_subscriptions", [])
        ]
        # 开赛提醒、整场结果、单图结果三类推送去重：{id: 推送时间}
        scheduler_state = state.data.get("scheduler_state", {})
        self._notified = {
            kind: scheduler_state.get(f"notified_{kind}", {}) for kind in NOTIFIED_KINDS
        }

    def _save(self, *, groups=None, subscriptions=None, notified=None) -> None:
        groups = self._groups if groups is None else groups
        subscriptions = self._global_subscriptions if subscriptions is None else subscriptions
        notified = self._notified if notified is None else notified
        # 只有写入成功才发布内存状态；失败交给调用方报告。
        atomic_write_json(self._data_file, {
            "groups": [asdict(g) for g in groups.values()],
            "global_subscriptions": [asdict(s) for s in subscriptions],
            "scheduler_state": {f"notified_{k}": v for k, v in notified.items()},
        })
        self._groups = groups
        self._global_subscriptions = subscriptions
        self._notified = notified

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

    def get_subscription(self, event_id: str) -> EventSubscription | None:
        return next((s for s in self._global_subscriptions if s.event_id == event_id), None)

    def update_subscription_meta(
        self, event_id: str, *, event_title: str,
        start_date: str, end_date: str,
    ) -> None:
        current = self.get_subscription(event_id)
        if current is not None:
            self._replace_subscription(replace(
                current, event_title=event_title,
                start_date=start_date, end_date=end_date,
            ))

    def set_mvp_status(self, event_id: str, status: str) -> None:
        current = self.get_subscription(event_id)
        if current is None:
            return
        since = datetime.now().isoformat() if status == "watching" else current.mvp_watch_since
        self._replace_subscription(replace(current, mvp_status=status, mvp_watch_since=since))

    def _replace_subscription(self, updated: EventSubscription) -> None:
        if updated == self.get_subscription(updated.event_id):
            return
        self._save(subscriptions=[
            updated if s.event_id == updated.event_id else s
            for s in self._global_subscriptions
        ])

    def is_notified(self, kind: NotifiedKind, item_id: str) -> bool:
        return item_id in self._notified[kind]

    def mark_notified(self, kind: NotifiedKind, item_id: str) -> None:
        self._save(notified={
            **self._notified,
            kind: {**self._notified[kind], item_id: datetime.now().isoformat()},
        })

    def cleanup_notified_state(self, ttl_days: int) -> int:
        cutoff = datetime.now() - timedelta(days=max(1, ttl_days))
        kept = {
            kind: {key: ts for key, ts in records.items() if datetime.fromisoformat(ts) >= cutoff}
            for kind, records in self._notified.items()
        }
        removed = sum(map(len, self._notified.values())) - sum(map(len, kept.values()))
        if removed:
            self._save(notified=kept)
        return removed


data_manager = DataManager()
