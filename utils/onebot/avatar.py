from utils.network.http import download_bytes
from utils.images.formats import validate_image
import hashlib
import time
from collections import OrderedDict

_cache = OrderedDict()


async def download_avatar(user_id: int, *, size=100):
    key = (int(user_id), size)
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        _cache.move_to_end(key)
        return cached[1]
    error = None
    for host in ("q1", "q2", "q4"):
        try:
            data = await download_bytes(
                f"https://{host}.qlogo.cn/g?b=qq&nk={int(user_id)}&s={size}",
                timeout=6,
                attempts=1,
                max_bytes=2 * 1024 * 1024,
            )
            validate_image(data)
            if (
                size == 640
                and hashlib.md5(data).hexdigest() == "acef72340ac0e914090bd35799f5594e"
            ):
                return await download_avatar(user_id, size=100)
            _cache[key] = (time.monotonic() + 3600, data)
            _cache.move_to_end(key)
            while len(_cache) > 1024:
                _cache.popitem(last=False)
            return data
        except Exception as exc:
            error = exc
    raise RuntimeError("Avatar download failed") from error
