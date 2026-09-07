from __future__ import annotations
from plugins.bili_dyn_sub.services import state as _state


def _build_login_expired_text(status: _state.LoginStatus) -> str:
    """失效告警文案：说清后果（功能不中断但风控概率上升）与具体处置动作"""
    return (
        "【B站动态订阅】登录态失效\n"
        f"状态：{status.summary()}\n"
        "已自动退化为匿名取数（功能不中断，但风控概率上升，可能出现 -352 空轮）。\n"
        "请重新获取小号的 SESSDATA 并填入 "
        "config/plugins/bili_dyn_sub/config.json 的 sessdata 字段后重启 bot。"
    )


async def _notify_superusers_login_expired(status: _state.LoginStatus) -> bool:
    """私聊提醒超管更换 sessdata。

    返回 False 仅代表「本次没能提醒、之后应重试」（当前无 Bot 连接）；
    未配置 superusers 属于配置问题、重试也没用，按已提醒处理避免每轮刷日志。
    """
    text = _build_login_expired_text(status)
    try:
        bot = _state.get_bot()
    except (ValueError, KeyError) as e:
        _state.logger.warning(
            f"登录态失效提醒暂时无法发送（无可用 Bot 连接），下次校验再试: {_state.get_exc_desc(e)}"
        )
        return False

    superusers = getattr(bot.config, "superusers", None) or []
    if not superusers:
        _state.logger.warning("B 站登录态已失效，但未配置 superusers，无法私聊提醒")
        return True

    for superuser in superusers:
        try:
            await bot.send_private_msg(user_id=int(superuser), message=text)
        except (ValueError, TypeError) as e:
            _state.logger.warning(
                f"超管 QQ 号 {superuser!r} 非法，跳过提醒: {_state.get_exc_desc(e)}"
            )
        except (_state.ActionFailed, _state.NetworkError) as e:
            _state.logger.warning(
                f"向超管 {superuser} 发送登录态失效提醒失败: {_state.get_exc_desc(e)}"
            )
    return True


async def _handle_login_transition(status: _state.LoginStatus) -> None:
    """按状态跃迁决定是否告警（有效→失效私聊超管；失效→有效只记日志）"""
    pass

    if not status.configured:
        # 未配置 sessdata：匿名取数是预期行为，没有「失效」可言
        _state._last_known_login = None
        return
    if status.error:
        # 网络抖动 / 限流 / 未知业务码：不是掉登录的证据（credential 已打 warning），不动基线
        return

    if status.is_login:
        if _state._last_known_login is False:
            _state.logger.info(
                f"B 站登录态已恢复（uname={status.uname or '-'}），无需再提醒超管"
            )
        _state._last_known_login = True
        return

    # 到这里是「配了 sessdata + 校验成功 + isLogin=false」，即确定需要人工更换
    if _state._last_known_login is False:
        _state.logger.debug("B 站登录态仍处于失效状态，已提醒过超管，不重复打扰")
        return
    _state.logger.warning(
        "B 站登录态失效：取数已退化为匿名请求（功能不中断但更易被风控），正在私聊提醒超管更换 sessdata"
    )
    if await _notify_superusers_login_expired(status):
        _state._last_known_login = False


async def check_login_status(*, force: bool = False) -> _state.LoginStatus:
    """校验登录态并按跃迁触发告警；返回本次结论。

    force=True 用于「怀疑掉登录」的即时确认（如取数意外返回 -101），会绕过 30 分钟缓存。
    未配置 sessdata 时 credential 侧不发任何请求，本函数等价于零开销。
    """
    status = await _state.credential_manager.verify_login(force=force)
    await _handle_login_transition(status)
    return status


async def confirm_login_after_auth_error() -> _state.LoginStatus:
    """取数返回 -101 时向 nav 确认真实登录态；**带冷却**，避免每轮都打 nav。

    -101 不会自动进退避（它不是风控），所以出现一次就会每轮复发。若每次都
    `force=True`，100s 的轮询间隔 × 每个 UID 会把 nav 打成一个高频请求（旧实现的
    「6 小时节流」在改成状态跃迁告警后一并丢掉了，这里补回节流，只是尺度小得多）。
    冷却窗口内退回普通校验：命中 30 分钟缓存则一个请求都不发，结论照样驱动跃迁告警。
    """
    pass
    now = _state.time.time()
    force = (
        now - _state._last_forced_login_check_ts
        >= _state.FORCED_LOGIN_CHECK_COOLDOWN_SECONDS
    )
    if force:
        _state._last_forced_login_check_ts = now
    return await check_login_status(force=force)


async def login_check_job() -> None:
    """周期任务：确认 sessdata 是否还在登录态（仅在配置了 sessdata 时注册）"""
    status = await check_login_status()
    _state.logger.debug(f"B 站登录态周期校验完成：{status.summary()}")


async def _startup_login_check() -> None:
    """启动后台校验：等 Bot 连上再校验一次，并把结论写进日志"""
    await _state.asyncio.sleep(_state.LOGIN_CHECK_STARTUP_DELAY_SECONDS)
    try:
        status = await check_login_status()
    except Exception as e:
        # 后台任务边界：任何意外都不能变成「Task exception was never retrieved」
        _state.logger.error(f"启动时校验 B 站登录态失败: {_state.get_exc_desc(e)}")
        return
    if not status.configured:
        _state.logger.info(
            "未配置 sessdata，B 站动态订阅将以匿名方式取数（风控概率略高）"
        )
    elif status.error:
        _state.logger.warning(f"启动时无法确认 B 站登录态：{status.summary()}")
    elif status.is_login:
        _state.logger.info(f"B 站登录态有效，账号={status.uname or '-'}")
    else:
        _state.logger.warning(
            "已配置 sessdata 但登录态无效，将退化为匿名取数（已提醒超管更换）"
        )
