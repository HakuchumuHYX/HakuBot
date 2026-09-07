from __future__ import annotations
from plugins.bili_dyn_sub.services import state as _state


def _track_empty_feed(uid: str, parsed_list: list[_state.ParsedDynamic]) -> int:
    """维护 per-UID 的连续空 feed 计数并在基线已建立时按阈值告警，返回当前连续次数。

    「code==0 且一条动态都没有」既可能是该 UP 真没动态，也可能是软风控
    （实测同一 UID 相邻两次请求分别返回 13 条与 0 条；刚重造的 buvid3 尤其容易吃这个）。
    基线已建立时它表现为「一直没有新动态」，不报错也不退避，需要日志给出线索。
    """
    if parsed_list:
        _state._empty_feed_streak.pop(uid, None)
        return 0

    streak = _state._empty_feed_streak.get(uid, 0) + 1
    _state._empty_feed_streak[uid] = streak
    if _state.store.is_baseline_initialized(uid):
        message = (
            f"UID {uid} 已连续 {streak} 轮取数成功但 feed 为空（既无 -352 也无动态），"
            f"疑似软风控；cookie 会随时间自愈，长期不恢复请在 config.json 配置 sessdata"
        )
        if streak == _state._EMPTY_FEED_WARN_THRESHOLD or (
            streak > _state._EMPTY_FEED_WARN_THRESHOLD
            and streak % _state._EMPTY_FEED_WARN_THRESHOLD == 0
        ):
            _state.logger.warning(message)
        else:
            _state.logger.debug(message)
    return streak


def _init_baseline(uid: str, parsed_list: list[_state.ParsedDynamic]) -> None:
    """首次订阅 / 状态缺失：全部标 seen，只 log 不推送（设计文档 §4.2）。

    空 feed 不立即建基线（见 _EMPTY_FEED_BASELINE_THRESHOLD 的说明）：
    连续 N 轮都是空的才认定这个 UP 真没有历史动态并写哨兵游标。
    """
    ids = [p.dyn_id for p in parsed_list if p.dyn_id]
    if not ids:
        streak = _state._empty_feed_streak.get(uid, 0)
        if streak < _state._EMPTY_FEED_BASELINE_THRESHOLD:
            _state.logger.info(
                f"UID {uid} 取数成功但没有任何动态（第 {streak}/{_state._EMPTY_FEED_BASELINE_THRESHOLD} 次），"
                "可能是软风控而非真的没动态，暂不建立基线"
            )
            return
        _state.store.init_baseline(uid, [], _state._EMPTY_FEED_CURSOR)
        _state._empty_feed_streak.pop(uid, None)
        _state.logger.info(
            f"UID {uid} 连续 {streak} 轮均无历史动态，已用哨兵游标建立基线，之后的新动态会正常推送"
        )
        return
    cursor = max((_state.dyn_id_to_int(i) or 0) for i in ids)
    _state.store.init_baseline(uid, ids, cursor)
    _state.logger.info(
        f"UID {uid} 首次建立基线：{len(ids)} 条历史动态标记为已读，本轮不推送"
    )


class PushSelection(_state.NamedTuple):
    """一轮筛选的结果。

    两个计数**故意分开**，因为它们的对外语义完全不同：
    - overflow：本轮新动态太多、被单轮条数上限压下去的条数 → 会向群里提示"另有 X 条未展示"，
      因为群友确实"错过了刚发生的事"，不提示反而像漏推；
    - stale：超出推送窗口（停机期间的积压）被丢弃的条数 → **只记日志，绝不进群消息**。
      默认窗口的全部意义就是"重启/迁移对群友无感"（见 config.max_dynamic_age_minutes），
      若还发一条"另有 X 条动态未展示"，等于把本该无声的停机公告出去，与该需求直接矛盾。
    """

    to_push: list[_state.ParsedDynamic]
    overflow: int
    stale: int


def _select_pushable(
    uid: str, parsed_list: list[_state.ParsedDynamic]
) -> PushSelection:
    """筛出真正要推的动态（升序）；被跳过/闸掉的一律只标 seen。

    两个抑制计数的差别见 PushSelection。二者都不含"跳过类型"与"未订阅分类"，
    那两类不属于"未展示的动态"，无需向用户提示。
    """
    fresh = [
        p for p in parsed_list if p.dyn_id and not _state.store.is_seen(uid, p.dyn_id)
    ]
    if not fresh:
        return PushSelection([], 0, 0)

    categories = set(_state.store.get_categories(uid))
    candidates: list[_state.ParsedDynamic] = []
    for parsed in fresh:
        if _state.should_skip(parsed):
            # 直播推荐/广告等：跳过推送但同样推进状态，否则每轮重复处理（§4.2）
            _state.logger.debug(
                f"UID {uid} 动态 {parsed.dyn_id} 类型 {parsed.dyn_type} 跳过推送，仅标记已读"
            )
            _state.store.mark_seen(uid, parsed.dyn_id, save=False)
            continue
        if parsed.category and parsed.category not in categories:
            _state.logger.debug(
                f"UID {uid} 动态 {parsed.dyn_id} 分类 {parsed.category} 未订阅，仅标记已读"
            )
            _state.store.mark_seen(uid, parsed.dyn_id, save=False)
            continue
        candidates.append(parsed)

    stale = 0
    overflow = 0

    # 闸一：推送窗口 —— 一条动态最多能"旧"到什么程度还值得推（max_dynamic_age_minutes）。
    # 「要不要补推停机期间遗漏的动态」不是独立开关，只是这个窗口取多大的自然结果：
    # 30 → 只推新鲜动态（停机期间遗漏的自然被丢弃）；1440 → 补推一天内的；0 → 不做时间限制。
    # 超窗口的一律**静默**标记已读：只记日志、不进群消息，避免下一轮反复处理。
    age_limit_seconds = _state._cfg_int("max_dynamic_age_minutes", 30, 0) * 60
    if age_limit_seconds > 0 and candidates:
        deadline = _state.time.time() - age_limit_seconds
        kept: list[_state.ParsedDynamic] = []
        for parsed in candidates:
            if parsed.pub_ts and parsed.pub_ts < deadline:
                _state.logger.info(
                    f"UID {uid} 动态 {parsed.dyn_id} 发布已超 {age_limit_seconds // 60}min，"
                    "超出推送窗口，仅标记已读"
                )
                _state.store.mark_seen(uid, parsed.dyn_id, save=False)
                stale += 1
            else:
                kept.append(parsed)
        candidates = kept

    # 闸二：单 UID 单轮最多推 N 条，多出的（较旧的那些）只标 seen（0 表示不限）。
    # 这是与"多旧才推"无关的独立关注点：防刷屏，且这部分会向群里提示"另有 X 条未展示"。
    max_count = _state._cfg_int("max_push_per_round", 5, 0)
    if max_count > 0 and len(candidates) > max_count:
        extra = candidates[:-max_count]
        candidates = candidates[-max_count:]
        for parsed in extra:
            _state.store.mark_seen(uid, parsed.dyn_id, save=False)
        overflow = len(extra)
        _state.logger.info(
            f"UID {uid} 本轮新动态超过 {max_count} 条上限，较旧的 {overflow} 条仅标记已读"
        )

    return PushSelection(candidates, overflow, stale)
