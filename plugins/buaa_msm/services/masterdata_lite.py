# plugins/buaa_msm/services/masterdata_lite.py
"""
统一 MasterData 读取器（buaa_msm 版）：
- 从 plugin_config.master_data_dir 读取 JSON
- 基于文件 mtime 自动重载
- 提供 id -> item/name/iconAssetbundleName 查询
- 提供音乐标题、唱片映射、角色数据查询
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot.log import logger

from ..config import plugin_config

_CATEGORY_MASTERDATA_FILE: dict[str, str] = {
    "material": "materials.json",
    "mysekai_material": "mysekaiMaterials.json",
    "mysekai_item": "mysekaiItems.json",
    "mysekai_fixture": "mysekaiFixtures.json",
    "mysekai_site_harvest_fixture": "mysekaiSiteHarvestFixtures.json",
    "musics": "musics.json",
    "mysekai_music_record": "mysekaiMusicRecords.json",
    "game_character_unit": "gameCharacterUnits.json",
    "mysekai_char_unit_group": "mysekaiGameCharacterUnitGroups.json",
}


class MasterDataLite:
    """
    轻量 MasterData 读取器：
    - 基于文件 mtime 自动重载
    - 提供 id -> 字段查询 + 领域专用方法
    """

    def __init__(self) -> None:
        self._table_cache: dict[str, list[dict[str, Any]]] = {}
        self._mtime_cache: dict[str, float] = {}
        self._index_cache: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}

    def _file_path(self, filename: str) -> Path:
        return plugin_config.master_data_dir / filename

    def _load_table(self, filename: str) -> list[dict[str, Any]]:
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
            raw = json.loads(path.read_text(encoding="utf-8"))
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

    def _build_id_index(self, category: str, filename: str) -> dict[int, dict[str, Any]]:
        cache_key = (category, filename)
        if cache_key in self._index_cache:
            return self._index_cache[cache_key]

        table = self._load_table(filename)
        ind: dict[int, dict[str, Any]] = {}
        for item in table:
            try:
                item_id = int(item.get("id"))
            except Exception:
                continue
            ind[item_id] = item

        self._index_cache[cache_key] = ind
        return ind

    # ============== 通用查询 ==============

    def get_item_by_id(self, category: str, item_id: int) -> dict[str, Any] | None:
        filename = _CATEGORY_MASTERDATA_FILE.get(category)
        if not filename:
            return None
        ind = self._build_id_index(category, filename)
        return ind.get(int(item_id))

    def get_icon_asset_name(self, category: str, item_id: int) -> str | None:
        item = self.get_item_by_id(category, item_id)
        if not item:
            return None
        for key in ("iconAssetbundleName", "assetbundleName", "thumbnailAssetbundleName"):
            val = item.get(key)
            if val:
                return str(val)
        return None

    def get_resource_name(self, category: str, item_id: int) -> str | None:
        item = self.get_item_by_id(category, item_id)
        if not item:
            return None
        for key in ("name", "resourceName", "displayName"):
            val = item.get(key)
            if val:
                return str(val)
        return None

    def get_harvest_fixture_meta(self, fixture_id: int) -> dict[str, Any] | None:
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

    # ============== 音乐/唱片查询 ==============

    def get_music_title(self, music_id: int) -> str | None:
        """通过 musics.json 的 id 查询音乐标题"""
        item = self.get_item_by_id("musics", music_id)
        if item:
            return item.get("title")
        return None

    def get_music_jacket_bundle(self, music_id: int) -> str | None:
        """通过 musics.json 的 id 查询 assetbundleName"""
        item = self.get_item_by_id("musics", music_id)
        if item:
            return item.get("assetbundleName")
        return None

    def _build_mysekai_music_map(self) -> dict[str, str]:
        """构建 mysekaiMusicRecordId → musics.externalId 映射"""
        table = self._load_table("mysekaiMusicRecords.json")
        result: dict[str, str] = {}
        for record in table:
            if record.get("mysekaiMusicTrackType") == "music":
                mysekai_id = str(record.get("id"))
                external_id = str(record.get("externalId"))
                result[mysekai_id] = external_id
        return result

    def get_music_title_by_record_id(self, mysekai_record_id: str) -> str | None:
        """通过 mysekaiMusicRecordId 获取音乐标题"""
        music_map = self._build_mysekai_music_map()
        external_id = music_map.get(str(mysekai_record_id))
        if not external_id:
            return None
        try:
            return self.get_music_title(int(external_id))
        except (ValueError, TypeError):
            return None

    def get_jacket_bundle_by_record_id(self, mysekai_record_id: str) -> str | None:
        """通过 mysekaiMusicRecordId 获取封面 assetbundleName"""
        music_map = self._build_mysekai_music_map()
        external_id = music_map.get(str(mysekai_record_id))
        if not external_id:
            return None
        try:
            return self.get_music_jacket_bundle(int(external_id))
        except (ValueError, TypeError):
            return None

    # ============== 角色数据查询 ==============

    def build_group_id_to_char_name(self, char_names: dict[int, str]) -> dict[str, str]:
        """
        构建 groupId → 角色名映射（仅单人组合）。

        需要关联两张表：
        - gameCharacterUnits.json: unitId → gameCharacterId
        - mysekaiGameCharacterUnitGroups.json: groupId → [unitId1, ...]
        """
        # unitId → gameCharacterId
        units_table = self._load_table("gameCharacterUnits.json")
        unit_to_char: dict[str, str] = {}
        for unit in units_table:
            unit_id = str(unit.get("id"))
            char_id = str(unit.get("gameCharacterId"))
            unit_to_char[unit_id] = char_id

        # groupId → [unitIds]
        groups_table = self._load_table("mysekaiGameCharacterUnitGroups.json")
        char_name_map = {str(k): v for k, v in char_names.items()}
        result: dict[str, str] = {}

        for group in groups_table:
            group_id = str(group.get("id"))
            unit_ids: list[str] = []
            for i in range(1, 6):
                key = f"gameCharacterUnitId{i}"
                if key in group:
                    unit_ids.append(str(group[key]))

            # 仅映射单人组合
            if len(unit_ids) == 1:
                char_id = unit_to_char.get(unit_ids[0])
                if char_id:
                    name = char_name_map.get(char_id)
                    if name:
                        result[group_id] = name

        return result


masterdata_lite = MasterDataLite()
