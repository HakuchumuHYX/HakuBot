from __future__ import annotations
from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown
from plugins.bili_dyn_sub.services import login as _login
from plugins.bili_dyn_sub.services import polling as _polling
from plugins.bili_dyn_sub.services import state as _state


def _register_jobs() -> None:
    """注册轮询与裁剪任务（import 即注册，幂等）"""
    interval = _state._cfg_int("poll_interval_seconds", 100, 30)
    jitter = _state._cfg_int("poll_jitter_seconds", 20, 0)
    try:
        _state.apscheduler.add_job(
            _polling.poll_once,
            trigger="interval",
            seconds=interval,
            jitter=jitter or None,
            id=_state.POLL_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _state.apscheduler.add_job(
            _polling.prune_state,
            trigger="cron",
            hour=4,
            minute=10,
            id=_state.PRUNE_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    except (ValueError, TypeError, LookupError) as e:
        _state.logger.error(f"注册 B 站动态轮询任务失败: {_state.get_exc_desc(e)}")
        return
    _state.logger.info(
        f"B 站动态轮询任务已注册：间隔 {interval}s（jitter {jitter}s），"
        f"当前订阅 {len(_state.store.get_all_uids())} 个 UID"
    )


def _register_login_check() -> None:
    """注册登录态校验：启动后确认一次 + 每 LOGIN_CHECK_INTERVAL_HOURS 小时确认一次。

    周期 job **仅在配置了 sessdata 时注册**：匿名取数没有「登录态失效」这回事，
    不配就一个 job、一次请求都不产生（启动那次校验在未配置时也是纯内存操作，
    只用来在日志里说明「本次以匿名方式取数」）。
    """
    if (_state.plugin_config.sessdata or "").strip():
        try:
            _state.apscheduler.add_job(
                _login.login_check_job,
                trigger="interval",
                hours=_state.LOGIN_CHECK_INTERVAL_HOURS,
                id=_state.LOGIN_CHECK_JOB_ID,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        except (ValueError, TypeError, LookupError) as e:
            _state.logger.error(
                f"注册 B 站登录态校验任务失败: {_state.get_exc_desc(e)}"
            )
        else:
            _state.logger.info(
                f"B 站登录态校验任务已注册：每 {_state.LOGIN_CHECK_INTERVAL_HOURS} 小时确认一次 sessdata 是否有效"
            )
    else:
        _state.logger.debug(
            "未配置 sessdata，不注册登录态校验周期任务（匿名取数无登录态可校验）"
        )

    try:
        driver = _state.get_driver()
    except ValueError as e:
        # 脱离 bot.py 单跑插件代码时没有 driver，周期任务仍在，只是少一次启动校验
        _state.logger.debug(
            f"当前没有 driver，跳过启动时的登录态校验: {_state.get_exc_desc(e)}"
        )
        return

    async def _on_startup() -> None:
        # 不能在启动钩子里直接 await：nav 请求最长 10s，且此刻 Bot 往往还没连上
        pass
        _state._startup_login_task = runtime.spawn(
            _login._startup_login_check(), name="bilibili-startup-login"
        )

    on_plugin_startup(driver, "bili_dyn_sub")(_on_startup)


_register_jobs()
_register_login_check()


@on_plugin_shutdown(_state.get_driver(), "bili_dyn_sub")
async def flush_subscription_state():
    await _state.credential_manager.flush()
    await _state._save_state()
