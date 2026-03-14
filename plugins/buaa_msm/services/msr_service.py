# plugins/buaa_msm/services/msr_service.py
"""
MSR 分析服务：

- 内部使用 domain 类型（UserDataContext / MSRRunResult）
- 对外提供：
  - run_msr_result(...) -> MSRRunResult（新接口）
  - run_msr(...) -> bool（兼容旧接口，供 handlers 直接用）
"""

from __future__ import annotations

from nonebot.exception import FinishedException
from nonebot.log import logger

from ..domain.models import MSRRunResult
from ..exceptions import DataLoadError, RenderError, SendError
from ..msr import execute_msr_analysis
from .user_data_service import get_user_context


async def run_msr_result(*, bot, user_id: str, event_user_id: int, send_func) -> MSRRunResult:
    # 先获取上下文，早点报错（保持提示一致）
    ctx_res = await get_user_context(user_id)
    if not ctx_res.ok or not ctx_res.ctx:
        raise DataLoadError(ctx_res.error or "unknown error")

    try:
        ok = await execute_msr_analysis(
            bot=bot,
            user_ctx=ctx_res.ctx,
            event_user_id=event_user_id,
            send_func=send_func,
        )
        if ok:
            return MSRRunResult(ok=True)
        raise RenderError("render returned false")
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"MSR 执行失败: {e}")
        try:
            await send_func("抱歉，分析生成失败。")
        except FinishedException:
            raise
        except Exception as send_err:
            logger.error(f"发送失败提示消息失败: {send_err}")
            raise SendError(str(send_err)) from e
        return MSRRunResult(ok=False, error=str(e))


async def run_msr(*, bot, user_id: str, event_user_id: int, send_func) -> bool:
    """
    兼容旧接口：返回 bool，供 handlers 直接使用。
    """
    res = await run_msr_result(bot=bot, user_id=user_id, event_user_id=event_user_id, send_func=send_func)
    return bool(res.ok)
