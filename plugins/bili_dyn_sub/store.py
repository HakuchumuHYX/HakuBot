"""bili_dyn_sub 订阅与去重状态，持久化在 data/bili_dyn_sub/state.json：

    {"subscriptions": {uid: {"name": str, "groups": [int]}},
     "cursors": {uid: int},
     "last_success": {uid: "2026-10-26T10:47:08"}}

去重只靠游标：动态 id 是雪花 id，随发布时间单调递增，<= 游标即已处理过。
置顶动态的 id 比游标小，自然被判为已读。没有游标的 UID 说明基线还没建立。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

from utils.json_io import atomic_write_json
from plugins.bili_dyn_sub.config import STATE_FILE

# 只有 last_success 变化时的最短写盘间隔；游标和订阅一变就立即写
LAST_SUCCESS_SAVE_INTERVAL = 1800.0


class Store:
    def __init__(self) -> None:
        raw = json.loads(STATE_FILE.read_text("utf-8")) if STATE_FILE.exists() else {}
        self._subscriptions: dict[str, dict] = raw.get("subscriptions", {})
        self._cursors: dict[str, int] = raw.get("cursors", {})
        self._last_success: dict[str, str] = raw.get("last_success", {})
        self._saved_at = 0.0

    async def save(self) -> None:
        # 快照在事件循环里拷好再交给线程，避免写盘期间状态被改
        snapshot = {
            "subscriptions": {
                uid: {"name": item["name"], "groups": list(item["groups"])}
                for uid, item in self._subscriptions.items()
            },
            "cursors": dict(self._cursors),
            "last_success": dict(self._last_success),
        }
        self._saved_at = time.monotonic()
        await asyncio.to_thread(atomic_write_json, STATE_FILE, snapshot)

    async def save_if_stale(self) -> None:
        """只有 last_success 变了时用：距上次写盘够久才写"""
        if time.monotonic() - self._saved_at >= LAST_SUCCESS_SAVE_INTERVAL:
            await self.save()

    # -------------------- 订阅 --------------------

    async def add_subscription(self, uid: str, name: str, group_id: int) -> None:
        item = self._subscriptions.setdefault(uid, {"name": name, "groups": []})
        item["name"] = name
        item["groups"] = sorted({*item["groups"], group_id})
        await self.save()

    async def remove_subscription(self, uid: str, group_id: int) -> bool:
        """退订某群；最后一个群退订时连同游标一起删掉"""
        item = self._subscriptions.get(uid)
        if item is None or group_id not in item["groups"]:
            return False
        item["groups"].remove(group_id)
        if not item["groups"]:
            del self._subscriptions[uid]
            self._cursors.pop(uid, None)
            self._last_success.pop(uid, None)
        await self.save()
        return True

    def uids(self) -> list[str]:
        return sorted(self._subscriptions)

    def groups(self, uid: str) -> list[int]:
        item = self._subscriptions.get(uid)
        return list(item["groups"]) if item else []

    def name(self, uid: str) -> str:
        item = self._subscriptions.get(uid)
        return item["name"] if item else ""

    def subscriptions(
        self, group_id: int | None = None
    ) -> list[tuple[str, str, list[int]]]:
        """(uid, 昵称, 群列表)；传 group_id 只列该群的"""
        return [
            (uid, item["name"], list(item["groups"]))
            for uid, item in sorted(self._subscriptions.items())
            if group_id is None or group_id in item["groups"]
        ]

    # -------------------- 去重 --------------------

    def has_baseline(self, uid: str) -> bool:
        return uid in self._cursors

    def is_seen(self, uid: str, dyn_id: str) -> bool:
        cursor = self._cursors.get(uid)
        return cursor is not None and int(dyn_id) <= cursor

    def advance(self, uid: str, dyn_id: str) -> bool:
        """把游标推到 dyn_id（只进不退），返回游标是否变化"""
        value = int(dyn_id)
        if value <= self._cursors.get(uid, -1):
            return False
        self._cursors[uid] = value
        return True

    def touch_success(self, uid: str) -> None:
        self._last_success[uid] = datetime.now().isoformat(timespec="seconds")

    def last_success(self, uid: str) -> str | None:
        return self._last_success.get(uid)


store = Store()
