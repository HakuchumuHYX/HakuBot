"""娶群友数据：当天的 CP、单身、透群友次数（daily.json），保护名单（list_protect.json）

当天记录带着日期，每次取用前比对，跨天整份清空；停机跨过 0 点也不会把昨天的记录带到今天。
改完记录由调用方 save()。
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from plugins.groupmate_waifu.config import DAILY_FILE, PROTECT_FILE
from utils.json_io import atomic_write_json


@dataclass
class Couple:
    husband: int
    # 被娶的一方：只有这一方 @ 对方时能锁 CP
    wife: int
    locked: bool = False

    def partner_of(self, user_id: int) -> int:
        return self.wife if user_id == self.husband else self.husband


@dataclass
class GroupRecord:
    couples: list[Couple] = field(default_factory=list)
    # 今天单身的人：自己不能再娶，别人指定娶必定成功
    singles: set[int] = field(default_factory=set)
    yinpa_by: Counter[int] = field(default_factory=Counter)
    yinpa_on: Counter[int] = field(default_factory=Counter)

    def couple_of(self, user_id: int) -> Couple | None:
        return next(
            (c for c in self.couples if user_id in (c.husband, c.wife)),
            None,
        )

    def has_result(self) -> set[int]:
        """今天已经有结果（成了 CP 或单身）的人，不再进娶群友卡池"""
        return self.singles | {u for c in self.couples for u in (c.husband, c.wife)}

    def marry(self, husband: int, wife: int) -> None:
        # 抢 CP 时把被抢的那对一起拆掉
        self.couples = [
            c for c in self.couples if not {c.husband, c.wife} & {husband, wife}
        ]
        self.singles -= {husband, wife}
        self.couples.append(Couple(husband, wife))


_date = ""
_groups: dict[int, GroupRecord] = {}
_protected: dict[int, set[int]] = {}


def _counter(data: dict) -> Counter[int]:
    return Counter({int(uid): times for uid, times in data.items()})


def load() -> None:
    global _date
    if DAILY_FILE.exists():
        data = json.loads(DAILY_FILE.read_text(encoding="utf-8"))
        _date = data["date"]
        _groups.clear()
        for gid, group in data["groups"].items():
            _groups[int(gid)] = GroupRecord(
                couples=[Couple(*couple) for couple in group["couples"]],
                singles=set(group["singles"]),
                yinpa_by=_counter(group["yinpa_by"]),
                yinpa_on=_counter(group["yinpa_on"]),
            )
    if PROTECT_FILE.exists():
        data = json.loads(PROTECT_FILE.read_text(encoding="utf-8"))
        _protected.clear()
        _protected.update({int(gid): set(uids) for gid, uids in data.items()})


def save() -> None:
    groups = {
        str(gid): {
            "couples": [[c.husband, c.wife, c.locked] for c in group.couples],
            "singles": sorted(group.singles),
            "yinpa_by": {str(uid): n for uid, n in group.yinpa_by.items()},
            "yinpa_on": {str(uid): n for uid, n in group.yinpa_on.items()},
        }
        for gid, group in _groups.items()
    }
    atomic_write_json(DAILY_FILE, {"date": _date, "groups": groups})


def reset() -> None:
    global _date
    _date = date.today().isoformat()
    _groups.clear()
    save()


def group(group_id: int) -> GroupRecord:
    if _date != date.today().isoformat():
        reset()
    return _groups.setdefault(group_id, GroupRecord())


def protected(group_id: int) -> set[int]:
    """返回的就是存着的集合，改完调用 save_protected()"""
    return _protected.setdefault(group_id, set())


def save_protected() -> None:
    atomic_write_json(
        PROTECT_FILE,
        {str(gid): sorted(uids) for gid, uids in _protected.items() if uids},
    )
