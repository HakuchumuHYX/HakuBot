from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nonebot.log import logger

from ..config import plugin_config

_CATEGORY_MASTERDATA_FILE: Dict[str, str] = {
    "mysekai_material": "mysekaiMaterials.json",
    "mysekai_item": "mysekaiItems.json",
    "mysekai_fixture": "mysekaiFixtures.json",
    "mysekai_site_harvest_fixture": "mysekaiSiteHarvestFixtures.json",
}


class MasterDataLite:
    """
    轻量 MasterData 读取器（buaa_msm 版）：
    - 从 plugin_config.master_data_dir 读取 JSON
    - 基于文件 mtime 自动重载
    - 提供 id -> item/name/iconAssetbundleName 查询
    """

    def __init__(self) -> None:
        self._table_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._mtime_cache: Dict[str, float] = {}
        self._index_cache: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}

    def _file_path(self, filename: str) -> Path:
        return plugin_config.master_data_dir / filename

    def _load_table(self, filename: str) -> List[Dict[str, Any]]:
        path = self._file_path(filename)
        if not path.exists():
            return []

        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []

        if filename in self._table_cache and self._mtime_cache.get(filename) == mtime:
            return self._table_cache[filename]

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            table = raw if isinstance(raw, list) else []
            self._table_cache[filename] = table
            self._mtime_cache[filename] = mtime

            # 文件更新后使该文件关联的索引失效
            stale_keys = [k for k in self._index_cache if k[1] == filename]
            for k in stale_keys:
                self._index_cache.pop(k, None)

            logger.debug(f"MasterDataLite 已加载 {filename}，记录数: {len(table)}")
            return table
        except Exception as e:
            logger.warning(f"MasterDataLite 读取失败 {path}: {e}")
            return []

    def _build_id_index(self, category: str, filename: str) -> Dict[int, Dict[str, Any]]:
        cache_key = (category, filename)
        if cache_key in self._index_cache:
            return self._index_cache[cache_key]

        table = self._load_table(filename)
        ind: Dict[int, Dict[str, Any]] = {}
        for item in table:
            try:
                item_id = int(item.get("id"))
            except Exception:
                continue
            ind[item_id] = item

        self._index_cache[cache_key] = ind
        return ind

    def get_item_by_id(self, category: str, item_id: int) -> Optional[Dict[str, Any]]:
        filename = _CATEGORY_MASTERDATA_FILE.get(category)
        if not filename:
            return None
        ind = self._build_id_index(category, filename)
        return ind.get(int(item_id))

    def get_icon_asset_name(self, category: str, item_id: int) -> Optional[str]:
        item = self.get_item_by_id(category, item_id)
        if not item:
            return None

        # 不同表可能字段名不同，按优先级兜底
        for key in ("iconAssetbundleName", "assetbundleName", "thumbnailAssetbundleName"):
            val = item.get(key)
            if val:
                return str(val)
        return None

    def get_resource_name(self, category: str, item_id: int) -> Optional[str]:
        item = self.get_item_by_id(category, item_id)
        if not item:
            return None

        # 常见命名字段优先级
        for key in ("name", "resourceName", "displayName"):
            val = item.get(key)
            if val:
                return str(val)
        return None

    def get_harvest_fixture_meta(self, fixture_id: int) -> Optional[Dict[str, Any]]:
        """
        查询 mysekaiSiteHarvestFixtures 的关键字段：
        - assetbundleName
        - mysekaiSiteHarvestFixtureRarityType
        - mysekaiSiteHarvestFixtureType
        """
        item = self.get_item_by_id("mysekai_site_harvest_fixture", fixture_id)
        if not item:
            return None

        asset = item.get("assetbundleName")
        rarity = item.get("mysekaiSiteHarvestFixtureRarityType")
        ftype = item.get("mysekaiSiteHarvestFixtureType")
        if not asset or rarity is None:
            return None

        return {
            "assetbundleName": str(asset),
            "rarity": str(rarity),
            "type": str(ftype) if ftype is not None else "",
        }


masterdata_lite = MasterDataLite()
