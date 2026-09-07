from __future__ import annotations
from plugins.bili_dyn_sub.services import delivery as _delivery
from plugins.bili_dyn_sub.services import login as _login
from plugins.bili_dyn_sub.services import selection as _selection
from plugins.bili_dyn_sub.services import state as _state


async def _fetch_feed(uid: str) -> _state.Optional[dict[str, _state.Any]]:
    """取一次 feed；成功返回 data 段，任何失败返回 None（调用方一律不推进状态）"""
    force_refresh = False
    for attempt in (1, 2):
        try:
            return await _state.api.fetch_space_feed(
                uid, force_refresh_cookie=force_refresh
            )
        except _state.api.BiliRiskControlError as e:
            action = _state.backoff_manager.on_risk_control(uid, "-352")
            if action == _state.ACTION_REFRESH_COOKIE and attempt == 1:
                _state.logger.info(
                    f"UID {uid} 触发 -352 风控，强制重造 cookie 后重试一次"
                )
                force_refresh = True
                continue
            # 刷新 cookie 后仍风控（或已达刷新上限）→ 进退避，本轮放弃
            remaining = _state.backoff_manager.remaining_seconds(uid)
            tail = f"退避 {remaining}s" if remaining else "下轮继续尝试重造 cookie"
            _state._log_error(
                uid,
                "-352",
                f"UID {uid} 持续被 -352 风控（{e}），{tail}；"
                "长期不恢复请在 config.json 配置 sessdata（小号登录态）",
            )
            return None
        except _state.api.BiliIpBlockedError as e:
            _state.backoff_manager.on_ip_block(uid, "HTTP 412")
            _state._log_error(
                uid,
                "412",
                f"UID {uid} 命中 IP 层风控（{e}），换 cookie 无效，"
                f"退避 {_state.backoff_manager.remaining_seconds(uid)}s；请在 config.json 配置 proxy",
            )
            return None
        except _state.api.BiliCaptchaError as e:
            # 需要 geetest 人机验证：不尝试自动过验证码，放弃本轮
            _state._log_error(
                uid,
                "captcha",
                f"UID {uid} 需要人机验证（{e}），放弃本轮；建议在 config.json 配置 sessdata",
            )
            return None
        except _state.api.BiliAuthError as e:
            # -101 只是「这次请求被当成未登录」，不能直接断言 sessdata 过期：
            # feed/space 对失效 sessdata 返回的是 code=0（静默降级为匿名），反过来 -101 也可能
            # 只是本次请求未带上登录字段。真实状态问 nav 接口，是否告警交给状态跃迁逻辑判断。
            _state._log_error(
                uid,
                "-101",
                f"UID {uid} 取数返回 -101（{e}），正在向 nav 接口确认真实登录态",
            )
            await _login.confirm_login_after_auth_error()
            return None
        except _state.api.BiliSignError as e:
            _state.api.invalidate_wbi_keys()
            if attempt == 1:
                _state.logger.info(
                    f"UID {uid} wbi 签名被拒（{e}），已作废 key 缓存后重试一次"
                )
                continue
            _state._log_error(
                uid, "-403", f"UID {uid} 刷新 wbi key 后仍签名失败（{e}），放弃本轮"
            )
            return None
        except _state.api.BiliNetworkError as e:
            _state.backoff_manager.on_network_error(uid, _state.get_exc_desc(e))
            _state._log_error(
                uid,
                "network",
                f"UID {uid} 取数网络异常（{e}），退避 {_state.backoff_manager.remaining_seconds(uid)}s 后重试",
            )
            return None
        except _state.api.BiliApiError as e:
            # 其他错误码 / 响应结构异常：不当作"没有动态"，本轮直接放弃
            _state._log_error(
                uid, f"api:{e.code}", f"UID {uid} 取数失败（{e}），本轮不推进状态"
            )
            return None
    return None


async def _poll_uid(bot: _state.Bot, uid: str) -> None:
    """处理单个 UID：取数 → 判新 → 闸门 → 渲染 → 分发"""
    if _state.backoff_manager.is_backing_off(uid):
        _state.logger.debug(
            f"UID {uid} 仍在退避中（剩余 {_state.backoff_manager.remaining_seconds(uid)}s），跳过本轮"
        )
        return

    data = await _fetch_feed(uid)
    if data is None:
        return  # 风控/网络/解析失败一律不动游标、不写 seen（§3.5）

    _state.backoff_manager.on_success(uid)
    _state.store.touch_last_success(uid, save=False)
    parsed_list = _state.parse_feed(data)
    _selection._track_empty_feed(uid, parsed_list)

    if not _state.store.is_baseline_initialized(uid):
        _selection._init_baseline(uid, parsed_list)  # 内部已落盘
        return

    to_push, overflow, stale = _selection._select_pushable(uid, parsed_list)
    # 先把「只标 seen 不推」的部分与 last_success 落盘
    await _state._save_state()

    if not to_push:
        if stale or overflow:
            _state.logger.info(
                f"UID {uid} 本轮新动态全部被闸门抑制（超窗口 {stale} 条 / 超条数上限 {overflow} 条），无需推送"
            )
        else:
            _state.logger.debug(f"UID {uid} 本轮无新动态")
        return

    # 推送目标**完全由订阅关系决定**：订阅关系即唯一开关，不再叠加任何群级开关。
    # 双层控制会造成"订阅列表里有、就是不推"的诡异状态（且超管一句「禁用all」就能让订阅静默失效），
    # 「不想收了」的正确操作是「b站退订」。完整理由见 docs/bili_dyn_sub_design.md §11.4.3。
    targets = _state.store.get_groups(uid)
    if not targets:
        for parsed in to_push:
            _state.store.mark_seen(uid, parsed.dyn_id, save=False)
        await _state._save_state()
        _state.logger.info(
            f"UID {uid} 有 {len(to_push)} 条新动态，但已无订阅群（可能刚被退订），仅标记已读"
        )
        return

    _state.logger.info(
        f"UID {uid} 发现 {len(to_push)} 条新动态，推送到 {len(targets)} 个群"
    )
    for parsed in to_push:
        # 推送前先写状态：渲染/发送失败最多重复 1 条，不会像 haruka-bot 那样永久卡死（§4.2）
        _state.store.mark_seen(uid, parsed.dyn_id, save=False)
        await _state._save_state()
        # 同一动态跨群只渲染一次（§5.3）
        segments = await _state.build_messages(parsed)
        for group_id in targets:
            await _delivery.dispatch_segments(
                bot, _delivery.SendTarget(group_id=group_id), segments
            )

    # 只有「刷屏保护压下去的条数」才提示群友；超出推送窗口的停机积压保持静默
    # （默认窗口的目的就是让重启/迁移对群友无感，提示一句等于把停机公告出去）
    if overflow > 0:
        tip = _state.Message(f"另有 {overflow} 条动态未展示")
        for group_id in targets:
            await _delivery._send_with_retry(
                bot, _delivery.SendTarget(group_id=group_id), tip, desc="未展示提示"
            )
    if stale > 0:
        _state.logger.info(
            f"UID {uid} 另有 {stale} 条动态超出推送窗口，已静默标记已读（不提示群友）"
        )


async def poll_once() -> None:
    """一轮完整轮询：UID 之间串行且错开，单个 UID 失败不影响其他 UID"""
    uids = _state.store.get_all_uids()
    if not uids:
        _state.logger.debug("暂无 B 站动态订阅，跳过本轮轮询")
        return

    try:
        bot = _state.get_bot()
    except (ValueError, KeyError) as e:
        _state.logger.info(
            f"当前没有可用的 Bot 连接，跳过本轮 B 站动态轮询: {_state.get_exc_desc(e)}"
        )
        return

    gap = _state._cfg_float("uid_request_gap_seconds", 8.0, 0.0)
    for index, uid in enumerate(uids):
        if index > 0 and gap > 0:
            # 绝不在同一 tick 并发打多个请求（设计文档 §6）
            await _state.asyncio.sleep(gap)
        try:
            await _poll_uid(bot, uid)
        except Exception as e:
            # 轮询边界：单个 UID 的任何意外都不能中断整轮
            _state.logger.exception(f"UID {uid} 本轮处理失败: {_state.get_exc_desc(e)}")


async def prune_state() -> None:
    """每日裁剪：清理已退订 uid 的残留状态与超限 seen_ids（§4.1）"""
    removed = await _state.run_in_pool(_state.store.prune)
    if removed:
        _state.logger.info(f"去重状态裁剪完成，清理 {removed} 条记录")
