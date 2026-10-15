# plugins/buaa_msm/infra/web_sessions.py
"""
网页登录会话（infra）

- 文件里只存 token 的 sha256，泄露了也不能直接拿来登录
- 会话 30 天过期；过期的在加载和读取时顺手删掉
"""

from __future__ import annotations

import hashlib
import secrets
import time

from nonebot import logger

from plugins.buaa_msm.config import plugin_config
from utils.json_io import atomic_write_json, load_json

SESSION_TTL_SECONDS = 30 * 24 * 3600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class WebSessionStore:
    def __init__(self):
        result = load_json(plugin_config.web_sessions_file, default={}, missing_ok=True)
        if not result.success:
            logger.error(f"加载网页会话失败，所有人需要重新登录: {result.error}")
        now = time.time()
        # {token_sha256: {"qq": "...", "created": epoch}}
        self._data: dict[str, dict] = {
            k: v
            for k, v in result.data.items()
            if now - v["created"] < SESSION_TTL_SECONDS
        }

    def _save(self) -> None:
        atomic_write_json(plugin_config.web_sessions_file, self._data)

    def create(self, qq: str) -> str:
        token = secrets.token_urlsafe(32)
        self._data[_hash(token)] = {"qq": qq, "created": time.time()}
        self._save()
        return token

    def get_qq(self, token: str) -> str | None:
        key = _hash(token)
        session = self._data.get(key)
        if session is None:
            return None
        if time.time() - session["created"] >= SESSION_TTL_SECONDS:
            del self._data[key]
            self._save()
            return None
        return session["qq"]

    def delete(self, token: str) -> None:
        if self._data.pop(_hash(token), None) is not None:
            self._save()


web_sessions = WebSessionStore()
