from __future__ import annotations
from plugins.bili_dyn_sub.services import state as _state


@_state.dataclass(frozen=True, slots=True)
class SendTarget:
    """一个发送目标：群聊或私聊二选一。

    推送走群聊；`b站订阅测试` 的渲染预览走私聊（把真实推送效果发给发起人自己，
    不打扰任何生产群）。两者共用同一套重试/间隔/合并转发降级逻辑，
    以保证预览与真实推送的分包节奏**逐条一致**。
    """

    group_id: _state.Optional[int] = None
    user_id: _state.Optional[int] = None

    @property
    def is_private(self) -> bool:
        return self.user_id is not None

    def __str__(self) -> str:
        return f"私聊 {self.user_id}" if self.is_private else f"群 {self.group_id}"


async def _send_with_retry(
    bot: _state.Bot,
    target: SendTarget,
    payload: _state.Message,
    *,
    desc: str = "",
) -> bool:
    """发一条消息，失败重试 send_retry_times 次；每次发送后固定 sleep 全局间隔。

    返回 False 仅代表"确认失败"（调用方可安全降级重发）；
    超时属于"可能已送达"，返回 True 以避免重复推送（同 utils.tools.send_forward_msg 的判断）。
    """
    attempts = max(
        _state._MIN_SEND_ATTEMPTS,
        _state._cfg_int("send_retry_times", 3, _state._MIN_SEND_ATTEMPTS),
    )
    interval = _state._cfg_float("send_interval_seconds", 1.5, 0.0)

    for attempt in range(1, attempts + 1):
        try:
            if target.is_private:
                await bot.send_private_msg(user_id=target.user_id, message=payload)
            else:
                await bot.send_group_msg(group_id=target.group_id, message=payload)
        except _state.NetworkError as e:
            await _state.asyncio.sleep(interval)
            if "timeout" in str(e).lower():
                _state.logger.warning(
                    f"{target} 发送{desc}超时，服务端可能已处理，跳过重试避免重复推送"
                )
                return True
            _state.logger.warning(
                f"{target} 发送{desc}网络错误（第 {attempt}/{attempts} 次）: {_state.get_exc_desc(e)}"
            )
        except (_state.ActionFailed, ValueError, _state.asyncio.TimeoutError) as e:
            await _state.asyncio.sleep(interval)
            _state.logger.warning(
                f"{target} 发送{desc}失败（第 {attempt}/{attempts} 次）: {_state.get_exc_desc(e)}"
            )
        else:
            # 全局节奏：发送成功后也要间隔，避免连发触发风控/限速
            await _state.asyncio.sleep(interval)
            return True

    _state.logger.error(f"{target} 发送{desc}最终失败，已重试 {attempts} 次")
    return False


async def dispatch_segments(
    bot: _state.Bot, target: SendTarget, segments: list[_state.MessageSegment]
) -> None:
    """按 §5.2 的拆包语义分发：首段单发 → 余下 1 段单发 / ≥2 段合并转发。

    公开给 `__init__.py` 的渲染预览复用：预览与真实推送必须走同一条分发路径，
    否则"预览看着没问题"就证明不了"推到群里也没问题"。
    """
    if not segments:
        return

    await _send_with_retry(
        bot, target, _state.Message(segments[0]), desc="动态文字卡片"
    )

    rest = segments[1:]
    if not rest:
        return
    if len(rest) == 1:
        await _send_with_retry(bot, target, _state.Message(rest[0]), desc="动态配图")
        return

    await _state.send_forward_msg(
        bot,
        group_id=target.group_id,
        user_id=target.user_id,
        items=[_state.Message(segment) for segment in rest],
        fallback_interval=_state._cfg_float("send_interval_seconds", 1.5, 0.0),
    )
