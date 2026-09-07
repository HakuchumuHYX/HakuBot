import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")
_pool = None


async def run_in_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """
    在线程池中运行同步函数
    """
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="haku-work")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, lambda: func(*args, **kwargs))


async def close_pool():
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)
