from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

import aiohttp
from nonebot.log import logger

from ..analysis import AggregatedData
from ..config import plugin_config
from ..exceptions import AssetDownloadError
from .masterdata_lite import masterdata_lite

_DYNAMIC_ICON_CATEGORIES: Set[str] = {
    "mysekai_material",
    "mysekai_item",
    "mysekai_fixture",
}


class RipAssetLite:
    """
    轻量 Rip 资源管理（buaa_msm 版）：
    - 仅负责 mysekai 动态 icon 的“下载到本地缓存 + 本地路径回查”
    - 提供统一的资源缓存查询接口（get_asset_cache_path），实现保持轻量可控
    """

    def __init__(self) -> None:
        self._locks: Dict[Tuple[str, int], asyncio.Lock] = {}

    def _get_lock(self, category: str, item_id: int) -> asyncio.Lock:
        key = (category, item_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _cache_path(self, category: str, item_id: int) -> Path:
        return plugin_config.mysekai_icon_cache_dir / category / f"{item_id}.png"

    def _harvest_fixture_cache_path(self, fixture_id: int) -> Path:
        return plugin_config.mysekai_icon_cache_dir / "mysekai_site_harvest_fixture" / f"{int(fixture_id)}.png"

    def get_cached_icon_path(self, category: str, item_id: int) -> Optional[str]:
        path = self._cache_path(category, int(item_id))
        if path.exists():
            return str(path)
        return None

    def get_cached_harvest_fixture_icon_path(self, fixture_id: int) -> Optional[str]:
        path = self._harvest_fixture_cache_path(int(fixture_id))
        if path.exists():
            return str(path)
        return None

    def _build_harvest_fixture_local_candidates(self, fixture_id: int) -> List[Path]:
        meta = masterdata_lite.get_harvest_fixture_meta(int(fixture_id))
        if not meta:
            return []

        rarity_raw = str(meta.get("rarity", "")).strip()
        asset_name = str(meta.get("assetbundleName", "")).strip()
        if not rarity_raw or not asset_name:
            return []

        rarity_dirs: List[str] = [rarity_raw]
        if rarity_raw.isdigit():
            rarity_dirs.append(f"rarity_{rarity_raw}")
        elif rarity_raw.startswith("rarity_"):
            plain = rarity_raw.replace("rarity_", "", 1)
            if plain.isdigit():
                rarity_dirs.append(plain)

        candidates: List[Path] = []
        for rarity_dir in rarity_dirs:
            candidates.append(plugin_config.data_dir / "harvest_fixture_icon" / rarity_dir / f"{asset_name}.png")
            candidates.append(plugin_config.resource_dir / "mysekai" / "harvest_fixture_icon" / rarity_dir / f"{asset_name}.png")
        return candidates

    def get_local_harvest_fixture_icon_path(self, fixture_id: int) -> Optional[str]:
        cache = self.get_cached_harvest_fixture_icon_path(int(fixture_id))
        if cache:
            return cache

        candidates = self._build_harvest_fixture_local_candidates(int(fixture_id))
        hit = next((p for p in candidates if p.exists()), None)
        return str(hit) if hit else None

    def _build_candidates(self, category: str, item_id: int) -> List[str]:
        item_id = int(item_id)
        asset_name = masterdata_lite.get_icon_asset_name(category, item_id)
        if not asset_name:
            return []

        # 严格按已核实字段 + 资产路径规则构造
        if category == "mysekai_material":
            return [f"mysekai/thumbnail/material/{asset_name}.png"]
        if category == "mysekai_item":
            return [f"mysekai/thumbnail/item/{asset_name}.png"]
        if category == "mysekai_fixture":
            # 先尝试 {asset}_{id}_1，再回退 {asset}_1
            return [
                f"mysekai/thumbnail/fixture/{asset_name}_{item_id}_1.png",
                f"mysekai/thumbnail/fixture/{asset_name}_1.png",
            ]
        return []

    def _build_url(self, relative_path: str, stage: str) -> str:
        base = plugin_config.mysekai_icon_url_base.rstrip("/")
        server = plugin_config.mysekai_icon_server
        return f"{base}/{server}-assets/{stage}/{relative_path}"

    def _build_url_candidates(self, relative_path: str) -> List[str]:
        # 当前资产规则中 mysekai 前缀属于 ondemand
        # 这里保留 startapp 作为兜底，便于未来资源迁移时自动适配
        return [
            self._build_url(relative_path, "ondemand"),
            self._build_url(relative_path, "startapp"),
        ]

    @staticmethod
    def _request_headers() -> Dict[str, str]:
        # 该资产站对“无 UA/Referer 的直连请求”会返回 403
        # Referer 使用当前配置的资产源自身，避免跨源伪装
        base = plugin_config.mysekai_icon_url_base.strip()
        parsed = urlsplit(base)
        referer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else f"{base.rstrip('/')}/"
        return {
            "User-Agent": "Mozilla/5.0",
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }

    @staticmethod
    def _is_retryable_http_status(status: int) -> bool:
        return status in (408, 409, 425, 429) or status >= 500

    @staticmethod
    def _is_retryable_exception(exc: Exception) -> bool:
        return isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError))

    async def _download_first_success(
        self,
        session: aiohttp.ClientSession,
        candidates: List[str],
        timeout: float,
    ) -> Tuple[Optional[bytes], List[str]]:
        errors: List[str] = []
        retries = max(0, int(plugin_config.mysekai_icon_download_retries))
        backoff = max(0.0, float(plugin_config.mysekai_icon_retry_backoff_seconds))
        max_attempts = retries + 1

        for rel in candidates:
            for url in self._build_url_candidates(rel):
                for attempt in range(1, max_attempts + 1):
                    try:
                        async with session.get(
                            url,
                            headers=self._request_headers(),
                            timeout=aiohttp.ClientTimeout(total=timeout),
                        ) as resp:
                            if resp.status == 200:
                                return await resp.read(), errors

                            errors.append(f"HTTP {resp.status} @ {url} (attempt {attempt}/{max_attempts})")
                            if not self._is_retryable_http_status(resp.status) or attempt >= max_attempts:
                                break
                    except Exception as e:
                        errors.append(f"{type(e).__name__} @ {url}: {e} (attempt {attempt}/{max_attempts})")
                        if not self._is_retryable_exception(e) or attempt >= max_attempts:
                            break

                    if backoff > 0:
                        await asyncio.sleep(backoff * (2 ** (attempt - 1)))

        return None, errors

    async def get_harvest_fixture_icon_path(
        self,
        session: aiohttp.ClientSession,
        fixture_id: int,
        *,
        allow_error: bool = True,
    ) -> Optional[str]:
        """
        Harvest fixture icon 走本地静态资源读取，不走 rip HTTP 下载。
        返回“本地可用路径”（缓存或静态目录）。
        """
        _ = session
        _ = allow_error
        return self.get_local_harvest_fixture_icon_path(int(fixture_id))

    async def get_asset_cache_path(
        self,
        session: aiohttp.ClientSession,
        category: str,
        item_id: int,
        *,
        allow_error: bool = True,
    ) -> Optional[str]:
        item_id = int(item_id)
        cache_path = self._cache_path(category, item_id)
        if cache_path.exists():
            return str(cache_path)

        if not plugin_config.mysekai_dynamic_icon_enabled:
            return None

        lock = self._get_lock(category, item_id)
        async with lock:
            # 双检，避免并发重复下载
            if cache_path.exists():
                return str(cache_path)

            candidates = self._build_candidates(category, item_id)
            if not candidates:
                return None

            data, errors = await self._download_first_success(
                session,
                candidates,
                timeout=plugin_config.mysekai_icon_download_timeout,
            )
            if data is None:
                if errors:
                    logger.warning(
                        f"MySekai icon 下载失败 [{category}:{item_id}]，候选={len(candidates)}，"
                        f"错误示例: {errors[:2]}"
                    )
                if allow_error:
                    return None
                raise AssetDownloadError(f"下载动态 icon 失败: {category}:{item_id}")

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
            return str(cache_path)

    async def prefetch_icons(self, items: Iterable[Tuple[str, int]]) -> Dict[Tuple[str, int], str]:
        """
        批量预取动态图标，返回成功缓存的本地路径映射。
        """
        wanted: Set[Tuple[str, int]] = {
            (c, int(i))
            for c, i in items
            if c in _DYNAMIC_ICON_CATEGORIES
        }
        result: Dict[Tuple[str, int], str] = {}
        if not wanted or not plugin_config.mysekai_dynamic_icon_enabled:
            return result

        sem = asyncio.Semaphore(max(1, int(plugin_config.mysekai_icon_prefetch_concurrency)))
        failed: List[Tuple[str, int]] = []

        async def _one(session: aiohttp.ClientSession, category: str, item_id: int) -> None:
            async with sem:
                p = await self.get_asset_cache_path(session, category, item_id, allow_error=True)
                if p:
                    result[(category, item_id)] = p
                else:
                    failed.append((category, item_id))

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*[_one(session, c, i) for c, i in wanted])

        logger.info(f"MySekai 动态 icon 预取完成: {len(result)}/{len(wanted)}")
        if failed:
            logger.warning(
                f"MySekai 动态 icon 预取失败数: {len(failed)}，失败示例: {failed[:5]}"
            )
        return result

    async def prefetch_harvest_fixture_icons(self, fixture_ids: Iterable[int]) -> Dict[int, str]:
        wanted: Set[int] = {int(x) for x in fixture_ids if x is not None}
        result: Dict[int, str] = {}
        if not wanted or not plugin_config.mysekai_dynamic_icon_enabled:
            return result

        sem = asyncio.Semaphore(max(1, int(plugin_config.mysekai_icon_prefetch_concurrency)))

        async def _one(session: aiohttp.ClientSession, fixture_id: int) -> None:
            async with sem:
                p = await self.get_harvest_fixture_icon_path(session, fixture_id, allow_error=True)
                if p:
                    result[fixture_id] = p

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*[_one(session, fid) for fid in wanted])

        # 该类别不进行远程下载；这里统计“本地可用命中”（缓存 + data 静态 + resources 静态）。
        logger.info(f"MySekai harvest fixture icon 本地可用命中: {len(result)}/{len(wanted)}")
        return result

    async def prefetch_from_analysis_data(self, analysis_data: AggregatedData) -> Dict[Tuple[str, int], str]:
        items: Set[Tuple[str, int]] = set()
        for summary in analysis_data.values():
            for (category, item_id_str), _ in (summary or {}).items():
                if category not in _DYNAMIC_ICON_CATEGORIES:
                    continue
                try:
                    items.add((category, int(item_id_str)))
                except Exception:
                    continue
        return await self.prefetch_icons(items)


rip_asset_lite = RipAssetLite()
