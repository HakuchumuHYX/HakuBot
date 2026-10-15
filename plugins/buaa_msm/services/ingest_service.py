# plugins/buaa_msm/services/ingest_service.py
"""
数据包入库：QQ 私聊上传和网页上传共用。

- reserve_storage_path：按绑定名生成目标文件名，并在存储目录里去重
- ingest_bin：文件落盘之后的步骤（更新最新文件索引 → 删旧文件 → 缓存失效 → 预解密 → 记录来访角色）
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from nonebot import logger

from plugins.buaa_msm.data_rename import generate_target_filename
from plugins.buaa_msm.infra.cache import cache_manager
from plugins.buaa_msm.infra.decryptor import decrypt_and_save
from plugins.buaa_msm.infra.storage import (
    file_storage_dir,
    remove_old_user_files,
    update_user_latest_file,
)
from plugins.buaa_msm.infra.visit_history import record_character_visit

# 渲染结果落盘的文件名，网页从这里读图
RESULT_FILES = {"summary": "msr_summary.png", "map": "msr_map.png"}


def user_output_dir(user_id: str) -> Path:
    return file_storage_dir / f"output_{user_id}"


def reserve_storage_path(original_filename: str, user_id: str) -> Path:
    """生成这次上传要写入的路径（与已有文件重名时加 _1、_2 后缀）"""
    file_storage_dir.mkdir(parents=True, exist_ok=True)
    filename = generate_target_filename(original_filename, user_id)
    base_name, ext = os.path.splitext(filename)
    destination = file_storage_dir / filename
    counter = 1
    while destination.exists():
        destination = file_storage_dir / f"{base_name}_{counter}{ext}"
        counter += 1
    return destination


async def ingest_bin(user_id: str, saved_path: Path) -> dict[str, Any] | None:
    """新数据包落盘后的处理；返回解密后的数据，解密失败返回 None"""
    update_user_latest_file(user_id, saved_path)
    remove_old_user_files(user_id, saved_path)
    await cache_manager.invalidate(user_id)

    # 旧的结果图属于上一份数据，先删掉，免得这次解密或渲染失败时网页还显示旧图
    output_dir = user_output_dir(user_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in RESULT_FILES.values():
        (output_dir / name).unlink(missing_ok=True)

    decrypted_data = await asyncio.to_thread(
        decrypt_and_save,
        bin_file_path=saved_path,
        json_output_path=output_dir / f"{saved_path.stem}_decrypted.json",
    )
    if decrypted_data:
        _record_visiting_characters(user_id, decrypted_data)
    return decrypted_data


def _record_visiting_characters(user_id: str, decrypted_data: dict):
    """记录来访角色"""
    try:
        char_visit_list = decrypted_data.get("userMysekaiGateCharacterVisit", {}).get(
            "userMysekaiGateCharacters", []
        )
        char_ids = []
        for char in char_visit_list:
            gid = char.get("mysekaiGameCharacterUnitGroupId")
            if gid:
                char_ids.append(str(gid))

        if char_ids:
            record_character_visit(user_id, char_ids)
            logger.debug(f"已记录用户 {user_id} 的 {len(char_ids)} 个来访角色ID")
    except Exception:
        logger.opt(exception=True).warning("记录来访角色失败")
