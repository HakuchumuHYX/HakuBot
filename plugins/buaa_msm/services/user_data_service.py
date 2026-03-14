# plugins/buaa_msm/services/user_data_service.py
"""
用户数据上下文获取：
- 优先使用缓存
- 缓存 miss 时：校验最新 bin -> (读取预解密 json 或即时解密) -> parse_map -> 写回缓存

重构说明：
- services 通过 infra/parsers/domain 进行解耦：
  - 解密：infra.decryptor
  - 解析：parsers.map_parser
  - 类型：domain.models
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from nonebot.log import logger

from ..domain.models import UserDataContext, UserDataResult
from ..infra.cache import cache_manager
from ..infra.decryptor import decrypt_and_save, load_decrypted_json
from ..infra.storage import file_storage_dir, user_latest_files
from ..parsers.map_parser import parse_map


async def get_user_context(user_id: str) -> UserDataResult:
    # cache
    cached = await cache_manager.get(user_id)
    if cached:
        user_output_dir = file_storage_dir / f"output_{user_id}"
        ctx = UserDataContext(
            user_id=user_id,
            decrypted_data=cached.decrypted_data or {},
            parsed_maps=cached.parsed_maps or {},
            latest_file_path=cached.file_path,
            user_output_dir=user_output_dir,
        )
        return UserDataResult(ok=True, ctx=ctx)

    # file exists?
    if user_id not in user_latest_files:
        return UserDataResult(ok=False, error="您还没有上传过文件，请先使用 'buaa上传文件' 命令。")

    latest_file_path = user_latest_files[user_id]
    if not latest_file_path.exists():
        return UserDataResult(ok=False, error="您的最新文件不存在，可能已被清理，请重新上传文件。")

    if latest_file_path.suffix.lower() != ".bin":
        return UserDataResult(ok=False, error="您上传的文件不是 .bin 格式，请上传正确的 mysekai 包体文件。")

    user_output_dir = file_storage_dir / f"output_{user_id}"
    user_output_dir.mkdir(parents=True, exist_ok=True)

    json_output_file = user_output_dir / f"{latest_file_path.stem}_decrypted.json"
    decrypted_data: Optional[Dict[str, Any]] = None

    # load pre-decrypted
    if json_output_file.exists():
        logger.info(f"正在加载预解密文件: {json_output_file.name}")
        decrypted_data = load_decrypted_json(json_output_file)
        if decrypted_data is None:
            logger.warning(f"加载预解密文件 {json_output_file.name} 失败")

    # decrypt now
    if decrypted_data is None:
        logger.info(f"正在即时解密: {latest_file_path.name}")
        decrypted_data = await asyncio.to_thread(
            decrypt_and_save,
            bin_file_path=latest_file_path,
            json_output_path=json_output_file,
        )
        if decrypted_data is None:
            return UserDataResult(ok=False, error="文件解密失败，请检查文件格式是否正确。")

    # parse maps
    parsed_maps = parse_map(decrypted_data)
    if parsed_maps is None:
        return UserDataResult(ok=False, error="地图数据解析失败。")

    # cache set
    await cache_manager.set(user_id, decrypted_data, parsed_maps, latest_file_path)

    ctx = UserDataContext(
        user_id=user_id,
        decrypted_data=decrypted_data,
        parsed_maps=parsed_maps,
        latest_file_path=latest_file_path,
        user_output_dir=user_output_dir,
    )
    return UserDataResult(ok=True, ctx=ctx)
