"""后台任务登记与资源关闭；插件启动失败时记进 health，开关据此拦截"""

import asyncio
import inspect
from functools import wraps
from nonebot import logger


class Lifecycle:
    def __init__(self):
        self.tasks = set()
        self.task_policies = {}
        self.closers = []
        self.stopping = False
        self.health = {}

    def spawn(self, coroutine, *, name: str, shutdown: str = "cancel"):
        if shutdown not in ("cancel", "drain", "owner"):
            coroutine.close()
            raise ValueError(f"未知的任务关闭策略: {shutdown}")
        if self.stopping:
            coroutine.close()
            raise RuntimeError("正在关闭，不再接收新任务")
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)
        self.task_policies[task] = shutdown
        task.add_done_callback(self._completed)
        return task

    def _completed(self, task):
        self.tasks.discard(task)
        self.task_policies.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            logger.opt(exception=task.exception()).error(
                f"后台任务异常退出: {task.get_name()}"
            )

    async def cancel_tasks(self):
        self.stopping = True
        tasks = tuple(self.tasks)
        for task in tasks:
            if self.task_policies.get(task) == "cancel":
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if self.task_policies.get(task) != "owner"),
            return_exceptions=True,
        )

    async def close(self):
        await self.cancel_tasks()
        remaining = tuple(self.tasks)
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        for close in reversed(self.closers):
            try:
                await close()
            except Exception:
                logger.exception("资源关闭失败")


runtime = Lifecycle()


def on_plugin_startup(driver, plugin_id):
    def register(function):
        @wraps(function)
        async def guarded():
            try:
                if inspect.iscoroutinefunction(function):
                    await function()
                else:
                    await asyncio.to_thread(function)
                runtime.health.setdefault(plugin_id, "ready")
            except Exception as exc:
                runtime.health[plugin_id] = f"unavailable: {type(exc).__name__}"
                logger.exception(f"插件启动失败: {plugin_id}")

        driver.on_startup(guarded)
        return function

    return register


def on_plugin_shutdown(driver, plugin_id):
    def register(function):
        @wraps(function)
        async def guarded():
            try:
                if inspect.iscoroutinefunction(function):
                    await function()
                else:
                    await asyncio.to_thread(function)
            except Exception:
                logger.exception(f"插件关闭失败: {plugin_id}")

        driver.on_shutdown(guarded)
        return function

    return register
