"""Owned asynchronous tasks and explicit resource shutdown."""

import asyncio
import inspect
from functools import wraps
from nonebot.log import logger


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
            raise ValueError("Unknown task shutdown policy")
        if self.stopping:
            coroutine.close()
            raise RuntimeError("Runtime is stopping")
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
                f"Background task failed: {task.get_name()}"
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
                logger.exception("Resource shutdown failed")


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
                logger.exception(f"Plugin initialization failed: {plugin_id}")

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
                logger.exception(f"Plugin shutdown failed: {plugin_id}")

        driver.on_shutdown(guarded)
        return function

    return register
