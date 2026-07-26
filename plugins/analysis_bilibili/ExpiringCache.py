import time


class ExpiringCache:
    """基于时间戳的过期缓存（无线程）

    expire_seconds <= 0 时表示不限制重复解析，set/get 均为 no-op。
    """

    def __init__(self, expire_seconds=0):
        # value -> 过期时间戳
        self.cache = {}
        self.expire_seconds = expire_seconds

    def _cleanup(self):
        # 惰性清理过期项，防止缓存膨胀
        now = time.monotonic()
        expired = [k for k, expire_at in self.cache.items() if expire_at <= now]
        for k in expired:
            del self.cache[k]

    def set(self, value):
        if self.expire_seconds <= 0:
            return
        self._cleanup()
        self.cache[value] = time.monotonic() + self.expire_seconds

    def get(self, value):
        if self.expire_seconds <= 0:
            return None
        self._cleanup()
        if value in self.cache:
            return value
        return None

    def __str__(self):
        return str(set(self.cache))
