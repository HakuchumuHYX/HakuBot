"""
Business service layer for groupmate_waifu.

Handlers should call this module instead of importing runtime records from
`data_manager` directly. Keep Bot API calls and message sending in handlers;
this layer owns state mutation, config access, and pure decision logic.
"""

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .data_manager import (
    HE,
    BE,
    NTR,
    last_sent_time_filter,
    yinpa_CP,
    yinpa_HE,
    reset_all_records,
    protect_list,
    record_CP,
    record_lock,
    record_waifu,
    record_yinpa1,
    record_yinpa2,
    save_protect_list,
    save_record_CP,
    save_record_lock,
    save_record_waifu,
    save_record_yinpa1,
    save_record_yinpa2,
)


# Configuration


def get_last_sent_time_filter() -> int:
    return last_sent_time_filter


def get_marriage_thresholds() -> Tuple[int, int, int]:
    return HE, BE, NTR


def get_yinpa_thresholds() -> Tuple[int, int]:
    return yinpa_HE, yinpa_CP


# Reset


def reset_records() -> None:
    reset_all_records()


# Protection


def get_protected_users(group_id: int) -> Set[int]:
    return set(protect_list.get(group_id, set()))


def is_protected(group_id: int, user_id: int) -> bool:
    return user_id in protect_list.get(group_id, set())


def protect_users(group_id: int, user_ids: Iterable[int]) -> None:
    protect_set = protect_list.setdefault(group_id, set())
    protect_set.update(user_ids)
    save_protect_list()


def unprotect_users(group_id: int, user_ids: Iterable[int]) -> Set[int]:
    protect_set = protect_list.setdefault(group_id, set())
    removed_users = protect_set & set(user_ids)
    if removed_users:
        protect_set.difference_update(removed_users)
        save_protect_list()
    return removed_users


# Couple state


def ensure_group_cp_records(group_id: int) -> Dict[int, int]:
    return record_CP.setdefault(group_id, {})


def get_partner(group_id: int, user_id: int) -> Optional[int]:
    return record_CP.get(group_id, {}).get(user_id)


def has_active_partner(group_id: int, user_id: int) -> bool:
    partner_id = get_partner(group_id, user_id)
    return bool(partner_id and partner_id != user_id)


def set_single(group_id: int, user_id: int) -> None:
    record_CP.setdefault(group_id, {})[user_id] = user_id
    save_record_CP()


def set_couple(group_id: int, user_id: int, partner_id: int) -> None:
    rec = record_CP.setdefault(group_id, {})
    rec[user_id] = partner_id
    rec[partner_id] = user_id
    record_waifu.setdefault(group_id, set()).add(partner_id)
    save_record_waifu()
    save_record_CP()


def remove_couple(group_id: int, user_id: int) -> Optional[int]:
    rec = record_CP.get(group_id)
    if not rec:
        return None

    partner_id = rec.get(user_id)
    if not partner_id or partner_id == user_id:
        return None

    rec.pop(user_id, None)
    rec.pop(partner_id, None)
    waifu_set = record_waifu.setdefault(group_id, set())
    waifu_set.discard(user_id)
    waifu_set.discard(partner_id)
    unlock_couple(group_id, user_id, partner_id)
    save_record_waifu()
    save_record_CP()
    return partner_id


def lock_couple(group_id: int, user_id: int, partner_id: int) -> None:
    lock = record_lock.setdefault(group_id, {})
    lock[user_id] = partner_id
    lock[partner_id] = user_id
    save_record_lock()


def unlock_couple(group_id: int, user_id: int, partner_id: int) -> None:
    if group_id not in record_lock:
        return
    record_lock[group_id].pop(user_id, None)
    record_lock[group_id].pop(partner_id, None)
    save_record_lock()


def is_couple_locked(group_id: int, user_id: int) -> bool:
    return user_id in record_lock.get(group_id, {})


def is_waifu_side(group_id: int, user_id: int) -> bool:
    return user_id in record_waifu.get(group_id, set())


def consume_single_status(group_id: int, user_id: int) -> bool:
    # A single marker gives targeted marriage HE odds once, then is consumed.
    rec = record_CP.get(group_id, {})
    if rec.get(user_id) != user_id:
        return False
    rec.pop(user_id, None)
    return True


def remove_displaced_partner(group_id: int, user_id: int) -> None:
    # NTR removes only the displaced partner side; the target is overwritten below.
    record_CP.get(group_id, {}).pop(user_id, None)
    record_waifu.setdefault(group_id, set()).discard(user_id)


# Yinpa


def record_yinpa(actor_id: int, target_id: int) -> None:
    record_yinpa1[actor_id] = record_yinpa1.get(actor_id, 0) + 1
    record_yinpa2[target_id] = record_yinpa2.get(target_id, 0) + 1
    save_record_yinpa1()
    save_record_yinpa2()


def resolve_yinpa_target(
    group_id: int,
    user_id: int,
    target_id: int,
    roll: int,
    cp_threshold: int,
    normal_threshold: int,
) -> Tuple[Optional[int], str, Optional[str]]:
    if is_protected(group_id, target_id):
        return None, "", "对方受到保护，不可以涩涩！"

    cp_id = get_partner(group_id, user_id)
    if target_id == cp_id:
        if 0 < roll <= cp_threshold:
            return target_id, "恭喜你涩到了你的老婆！", None
        return None, "", "你的老婆拒绝和你涩涩！"

    if 0 < roll <= normal_threshold:
        return target_id, "恭喜你涩到了群友！", None
    return None, "", "涩涩警察出现！不许涩涩！"


def get_yinpa_record_rows(
    member_list: Iterable[Dict[str, Any]],
    record: Dict[int, int],
) -> List[Tuple[str, int]]:
    rows = [
        ((member["card"] or member["nickname"]), times)
        for member in member_list
        if (times := record.get(member["user_id"]))
    ]
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


def get_yinpa_actor_record_rows(member_list: Iterable[Dict[str, Any]]) -> List[Tuple[str, int]]:
    return get_yinpa_record_rows(member_list, record_yinpa1)


def get_yinpa_target_record_rows(member_list: Iterable[Dict[str, Any]]) -> List[Tuple[str, int]]:
    return get_yinpa_record_rows(member_list, record_yinpa2)


# Marriage


def resolve_marriage_target(
    group_id: int,
    user_id: int,
    target_id: int,
    roll: int,
    happy_threshold: int,
    bad_threshold: int,
) -> Tuple[Optional[int], str, Optional[str]]:
    if target_id == user_id:
        return None, "", "不可以娶自己哦！"

    if consume_single_status(group_id, target_id):
        roll = happy_threshold

    if 0 < roll <= happy_threshold:
        return target_id, "恭喜你娶到了群友!\n你的群友結婚对象是、", None
    if happy_threshold < roll <= bad_threshold:
        return user_id, "你的群友結婚对象是、", None
    return None, "", "TARGET_FAILED"


def resolve_taken_marriage_target(
    group_id: int,
    user_id: int,
    target_id: int,
    roll: int,
    ntr_threshold: int,
) -> str:
    current_partner = get_partner(group_id, target_id)
    if current_partner is None:
        return "available"

    if is_couple_locked(group_id, target_id):
        set_single(group_id, user_id)
        return "locked"

    if roll > ntr_threshold:
        set_single(group_id, user_id)
        return "failed"

    remove_displaced_partner(group_id, current_partner)
    return "ntr"


# Pool and list queries


def get_marriage_pool_members(
    group_id: int,
    member_list: Iterable[Dict[str, Any]],
    last_sent_after: int,
) -> List[Dict[str, Any]]:
    cp_records = record_CP.get(group_id, {})
    rule_out = get_protected_users(group_id) | set(cp_records.keys())
    return [
        member for member in member_list
        if member["user_id"] not in rule_out
        and member["last_sent_time"] > last_sent_after
    ]


def get_yinpa_pool_members(
    group_id: int,
    member_list: Iterable[Dict[str, Any]],
    last_sent_after: int,
    excluded_user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    protect_set = get_protected_users(group_id)
    return [
        member for member in member_list
        if member["user_id"] not in protect_set
        and member["last_sent_time"] > last_sent_after
        and (excluded_user_id is None or member["user_id"] != excluded_user_id)
    ]


def get_cp_pairs(group_id: int) -> List[Tuple[int, int]]:
    rec = record_CP.get(group_id, {})
    return [
        (user_id, waifu_id)
        for waifu_id in record_waifu.get(group_id, set())
        if (user_id := rec.get(waifu_id))
    ]
