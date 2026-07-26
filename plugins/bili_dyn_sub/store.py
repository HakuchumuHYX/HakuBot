"""bili_dyn_sub 订阅与去重状态持久化（设计文档 §4.1 / §4.2）。

data/bili_dyn_sub/state.json 结构：
    {"subscriptions": {uid: {"name": str, "groups": [int], "categories": [int]}},
     "seen": {uid: {"cursor": int, "ids": [dyn_id]}},
     "last_success": {uid: "2026-07-26T20:05:00"}}

约定：
- uid / dyn_id 一律用 str 作 key（B 站 id_str 会超出 JS 安全整数范围，字符串更保险）。
- 判新走「游标 + seen_ids」双保险：int(dyn_id) <= cursor 或命中 ids 即视为已推送，
  可挡住置顶动态、翻页重叠与乱序；非纯数字 id 只入 ids、不动游标。
- seen 挂在 uid 全局粒度，推送目标（群列表）单独存，一份状态多处分发。
- 读盘失败 / 文件损坏只记日志并回落空结构，绝不把异常抛给调用方。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..utils.json_io import atomic_write_json
from ..utils.tools import get_logger
from .config import STATE_FILE, plugin_config

logger = get_logger("bili_dyn_sub.store")

# 动态分类（设计文档 §7 的 categories 映射）
DEFAULT_CATEGORIES: list[int] = [1, 2, 3, 4, 5, 6]
_VALID_CATEGORIES: frozenset[int] = frozenset(DEFAULT_CATEGORIES)

# 配置缺失 / 非法时的兜底值
_FALLBACK_SEEN_IDS_MAX = 50
_FALLBACK_SEEN_RETENTION_DAYS = 14


def _config_int(name: str, default: int, minimum: int = 1) -> int:
    """读取整数配置项，缺失或非法时回落默认值（store 不应因配置问题崩掉）"""
    raw = getattr(plugin_config, name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(f"配置项 {name}={raw!r} 不是整数，回落为 {default}")
        return default
    return max(minimum, value)


def dyn_id_to_int(dyn_id: Any) -> Optional[int]:
    """动态 id 转 int；非纯数字（B 站偶发的非雪花 id）返回 None 而不抛异常"""
    try:
        return int(str(dyn_id).strip())
    except (TypeError, ValueError):
        return None


def _normalize_int_list(raw: Any, allowed: Optional[frozenset[int]] = None) -> list[int]:
    """归一化整数列表：丢弃非数字与不在 allowed 内的项，去重后升序"""
    if not isinstance(raw, (list, tuple, set)):
        return []
    picked = {
        value
        for value in (dyn_id_to_int(item) for item in raw)
        if value is not None and (allowed is None or value in allowed)
    }
    return sorted(picked)


def _normalize_categories(raw: Any) -> list[int]:
    """归一化分类列表：只保留 1-6 的合法值，为空则视为全分类"""
    return _normalize_int_list(raw, _VALID_CATEGORIES) or list(DEFAULT_CATEGORIES)


def _seen_sort_key(dyn_id: str) -> tuple[int, int]:
    """seen_ids 排序键：按 id 数值升序，非纯数字 id 视为最新（截断时优先保留）"""
    value = dyn_id_to_int(dyn_id)
    return (1, 0) if value is None else (0, value)


class Store:
    """订阅 + 去重状态容器；模块级单例见文件末尾的 store"""

    def __init__(self, state_file: Path = STATE_FILE) -> None:
        self._state_file: Path = Path(state_file)
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._seen: dict[str, dict[str, Any]] = {}
        self._last_success: dict[str, str] = {}
        self.load()

    # -------------------- 读写 --------------------

    def load(self) -> None:
        """从 state.json 载入；文件缺失/损坏时回落空结构并记日志"""
        self._subscriptions = {}
        self._seen = {}
        self._last_success = {}

        if not self._state_file.exists():
            logger.debug(f"状态文件不存在，以空状态启动: {self._state_file}")
            return

        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error(f"读取状态文件失败，本次以空状态运行（原文件保留待人工检查）: {e}")
            return

        if not isinstance(raw, dict):
            logger.error("状态文件顶层不是对象，本次以空状态运行")
            return

        self._subscriptions = self._parse_subscriptions(raw.get("subscriptions"))
        self._seen = self._parse_seen(raw.get("seen"))
        self._last_success = self._parse_last_success(raw.get("last_success"))
        logger.debug(
            f"状态载入完成: {len(self._subscriptions)} 个订阅 UID, {len(self._seen)} 份去重记录"
        )

    @staticmethod
    def _parse_subscriptions(raw: Any) -> dict[str, dict[str, Any]]:
        """解析 subscriptions 段，逐条容错（坏条目跳过而不是整体失败）"""
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for uid, item in raw.items():
            if not isinstance(item, dict):
                logger.warning(f"订阅条目 {uid} 不是对象，已跳过")
                continue
            groups = _normalize_int_list(item.get("groups"))
            if not groups:
                logger.warning(f"订阅条目 {uid} 没有有效群号，已跳过")
                continue
            name = item.get("name")
            out[str(uid)] = {
                "name": name if isinstance(name, str) else "",
                "groups": groups,
                "categories": _normalize_categories(item.get("categories")),
            }
        return out

    @staticmethod
    def _parse_seen(raw: Any) -> dict[str, dict[str, Any]]:
        """解析 seen 段：cursor 非数字回落 0，ids 只保留字符串化后的动态 id"""
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for uid, item in raw.items():
            if not isinstance(item, dict):
                logger.warning(f"去重条目 {uid} 不是对象，已跳过")
                continue
            cursor = dyn_id_to_int(item.get("cursor")) or 0
            ids_raw = item.get("ids")
            ids = [str(i) for i in ids_raw] if isinstance(ids_raw, list) else []
            out[str(uid)] = {"cursor": max(0, cursor), "ids": ids}
        return out

    @staticmethod
    def _parse_last_success(raw: Any) -> dict[str, str]:
        """解析 last_success 段，只保留 ISO 字符串值"""
        if not isinstance(raw, dict):
            return {}
        return {str(uid): value for uid, value in raw.items() if isinstance(value, str)}

    def to_dict(self) -> dict[str, Any]:
        """导出当前状态的**快照副本**（供落盘与调试，改它不会影响内部状态）。

        必须是副本：save() 会被 run_in_pool 丢到线程里执行（scheduler._save_state /
        prune_state），若把内部 dict/list 直接交给 json.dump，事件循环侧的 mark_seen
        追加一个 id 就会让线程里抛 RuntimeError: dictionary changed size during iteration。
        """
        return {
            "subscriptions": {
                uid: {**item, "groups": list(item["groups"]), "categories": list(item["categories"])}
                for uid, item in self._subscriptions.items()
            },
            "seen": {
                uid: {"cursor": entry.get("cursor", 0), "ids": list(entry.get("ids") or [])}
                for uid, entry in self._seen.items()
            },
            "last_success": dict(self._last_success),
        }

    def save(self) -> bool:
        """同步原子写盘；调用方负责别在热路径高频调用。失败只记日志并返回 False"""
        try:
            atomic_write_json(self._state_file, self.to_dict())
            return True
        except (OSError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"写入状态文件失败，本次修改仅存在内存中: {e}")
            return False

    # -------------------- 订阅管理 --------------------

    def add_subscription(
        self,
        uid: str,
        name: str,
        group_id: int,
        categories: Optional[list[int]] = None,
    ) -> bool:
        """新增订阅；已存在则把群号并入 groups 去重。

        返回 True 表示该群是新订阅，False 表示此前已订阅过（仅刷新 name/categories）。
        """
        uid = str(uid)
        gid = dyn_id_to_int(group_id)
        if gid is None:
            logger.error(f"群号非法，忽略订阅操作: uid={uid} group={group_id!r}")
            return False
        group_id = gid
        item = self._subscriptions.get(uid)

        if item is None:
            self._subscriptions[uid] = {
                "name": name or "",
                "groups": [group_id],
                "categories": _normalize_categories(categories),
            }
            self.save()
            logger.info(f"新增订阅 uid={uid} name={name!r} group={group_id}")
            return True

        added = group_id not in item["groups"]
        if added:
            item["groups"] = sorted(set(item["groups"]) | {group_id})
        if name:
            item["name"] = name
        if categories is not None:
            item["categories"] = _normalize_categories(categories)
        self.save()
        logger.info(f"更新订阅 uid={uid} group={group_id} 新增={added}")
        return added

    def remove_subscription(self, uid: str, group_id: int) -> bool:
        """退订某群；groups 空了则整条删除，并清掉 seen / last_success"""
        uid = str(uid)
        group_id = dyn_id_to_int(group_id)
        item = self._subscriptions.get(uid)
        if group_id is None or item is None or group_id not in item["groups"]:
            return False

        item["groups"] = [g for g in item["groups"] if g != group_id]
        if not item["groups"]:
            self._subscriptions.pop(uid, None)
            self._seen.pop(uid, None)
            self._last_success.pop(uid, None)
            logger.info(f"退订 uid={uid} group={group_id}，已无订阅群，清除去重状态")
        else:
            logger.info(f"退订 uid={uid} group={group_id}，剩余群 {item['groups']}")
        self.save()
        return True

    def list_subscriptions(self, group_id: Optional[int] = None) -> list[dict[str, Any]]:
        """列出订阅；传 group_id 只列该群的，不传则全量。返回副本，调用方可安全修改"""
        wanted = dyn_id_to_int(group_id) if group_id is not None else None
        result: list[dict[str, Any]] = []
        for uid, item in sorted(self._subscriptions.items()):
            groups = list(item["groups"])
            if wanted is not None and wanted not in groups:
                continue
            result.append(
                {
                    "uid": uid,
                    "name": item.get("name", ""),
                    "groups": groups,
                    "categories": list(item["categories"]),
                }
            )
        return result

    def get_all_uids(self) -> list[str]:
        """按 uid 聚合的订阅列表（供调度轮转用）"""
        return sorted(self._subscriptions.keys())

    def get_groups(self, uid: str) -> list[int]:
        """某 uid 的推送目标群列表"""
        item = self._subscriptions.get(str(uid))
        return list(item["groups"]) if item else []

    def get_categories(self, uid: str) -> list[int]:
        """某 uid 订阅的动态分类；未订阅时返回全分类"""
        item = self._subscriptions.get(str(uid))
        return list(item["categories"]) if item else list(DEFAULT_CATEGORIES)

    def get_name(self, uid: str) -> str:
        """某 uid 记录的 UP 主昵称，缺失返回空串"""
        item = self._subscriptions.get(str(uid))
        return str(item.get("name", "")) if item else ""

    # -------------------- 去重状态 --------------------

    def is_baseline_initialized(self, uid: str) -> bool:
        """基线是否已建立：seen[uid] 存在且 cursor > 0"""
        entry = self._seen.get(str(uid))
        return bool(entry) and (dyn_id_to_int(entry.get("cursor")) or 0) > 0

    def init_baseline(self, uid: str, ids: list[str], cursor: int) -> None:
        """首次订阅 / 状态缺失时建基线：全部标 seen，只回执不推送"""
        uid = str(uid)
        limit = _config_int("seen_ids_max", _FALLBACK_SEEN_IDS_MAX)
        # 去重后按 id 数值升序（与 mark_seen 的「新的在尾部」一致），超限只留最新的
        str_ids = sorted(dict.fromkeys(str(i) for i in ids), key=_seen_sort_key)[-limit:]
        numeric = [v for v in (dyn_id_to_int(i) for i in str_ids) if v is not None]
        # 显式取全量 max，避免置顶动态把游标压低
        final_cursor = max([dyn_id_to_int(cursor) or 0, *numeric, 0])
        self._seen[uid] = {"cursor": final_cursor, "ids": str_ids}
        self.save()
        logger.info(f"建立基线 uid={uid} cursor={final_cursor} ids={len(str_ids)} 条")

    def is_seen(self, uid: str, dyn_id: str) -> bool:
        """游标 + seen_ids 双条件判断是否已推送过"""
        entry = self._seen.get(str(uid))
        if not entry:
            return False
        dyn_id = str(dyn_id)
        if dyn_id in entry["ids"]:
            return True
        value = dyn_id_to_int(dyn_id)
        cursor = dyn_id_to_int(entry.get("cursor")) or 0
        return value is not None and cursor > 0 and value <= cursor

    def mark_seen(self, uid: str, dyn_id: str, *, save: bool = True) -> None:
        """标记已处理：入有界 FIFO 的 ids 并推进游标。

        推送前先调用（渲染/推送失败最多重复 1 条，不会永久卡死）；
        跳过类型（LIVE_RCMD / AD 等）也要调用，否则每轮重复处理。
        """
        uid = str(uid)
        dyn_id = str(dyn_id)
        entry = self._seen.setdefault(uid, {"cursor": 0, "ids": []})

        if dyn_id not in entry["ids"]:
            entry["ids"].append(dyn_id)
        limit = _config_int("seen_ids_max", _FALLBACK_SEEN_IDS_MAX)
        if len(entry["ids"]) > limit:
            # FIFO：丢最旧的，保留最新的 limit 条
            del entry["ids"][: len(entry["ids"]) - limit]

        value = dyn_id_to_int(dyn_id)
        if value is None:
            # 非纯数字 id 只入 ids，不动游标，避免污染判新条件
            logger.warning(f"动态 id 非纯数字，仅记录到 seen_ids: uid={uid} id={dyn_id!r}")
        else:
            entry["cursor"] = max(dyn_id_to_int(entry.get("cursor")) or 0, value)

        if save:
            self.save()

    def touch_last_success(self, uid: str, *, save: bool = True) -> None:
        """记录一次成功取数的时间（仅 code==0 且拿到 items 时调用）"""
        self._last_success[str(uid)] = datetime.now().isoformat(timespec="seconds")
        if save:
            self.save()

    def get_last_success(self, uid: str) -> Optional[str]:
        """上次成功取数时间（ISO 字符串），无记录返回 None"""
        return self._last_success.get(str(uid))

    def prune(self) -> int:
        """裁剪去重状态：ids 超上限截断保留最新的；已退订且过期的 uid 状态整体清除。

        仍在订阅中的 uid 无论多久没成功取数都保留基线，否则会退化成重新建基线（会吞动态）。
        返回被裁掉的记录数（截断的 id 数 + 清除的 uid 状态数）。
        """
        limit = _config_int("seen_ids_max", _FALLBACK_SEEN_IDS_MAX)
        retention_days = _config_int("seen_retention_days", _FALLBACK_SEEN_RETENTION_DAYS)
        cutoff = datetime.now() - timedelta(days=retention_days)

        trimmed_ids = 0
        dropped_uids: list[str] = []

        for uid, entry in list(self._seen.items()):
            if uid not in self._subscriptions:
                last = self._last_success.get(uid)
                stale = True
                if last:
                    try:
                        stale = datetime.fromisoformat(last) < cutoff
                    except ValueError:
                        logger.warning(f"last_success 时间格式非法，按过期处理: uid={uid} value={last!r}")
                if stale:
                    self._seen.pop(uid, None)
                    self._last_success.pop(uid, None)
                    dropped_uids.append(uid)
                    continue

            ids: list[str] = entry["ids"]
            if len(ids) > limit:
                trimmed_ids += len(ids) - limit
                del ids[: len(ids) - limit]

        # 清理没有对应订阅与去重状态的孤立时间戳
        for uid in list(self._last_success.keys()):
            if uid not in self._subscriptions and uid not in self._seen:
                self._last_success.pop(uid, None)

        removed = trimmed_ids + len(dropped_uids)
        if removed:
            self.save()
            logger.info(f"裁剪去重状态: 截断 {trimmed_ids} 条 id，清除 {len(dropped_uids)} 个失效 UID 状态")
        return removed


# 模块级单例
store = Store()
