# plugins/buaa_msm/infra/decryptor.py
"""
解密模块：负责 sssekai 加密包体的解密、JSON 保存与加载。
"""

from __future__ import annotations

import json
import msgpack
from pathlib import Path
from typing import Any, Dict

from nonebot.log import logger

# 尝试导入 sssekai，如果失败则提供明确提示
try:
    from sssekai.crypto.APIManager import decrypt as _sssekai_decrypt, SEKAI_APIMANAGER_KEYSETS

    _SSSEKAI_LOADED = True
except ImportError:
    _SSSEKAI_LOADED = False
    logger.error("关键依赖 'sssekai' 未安装！请执行: pip install sssekai")


def decrypt_packet(infile: Path, region: str = "jp") -> dict[str, Any] | None:
    """解密 sssekai 加密的数据包 (bin) 并返回 Python 字典。"""
    if not _SSSEKAI_LOADED:
        logger.error("decrypt_packet 调用失败: 'sssekai' 模块未加载。")
        return None

    try:
        data = infile.read_bytes()
        plain = _sssekai_decrypt(data, SEKAI_APIMANAGER_KEYSETS[region])
        try:
            return msgpack.unpackb(plain)
        except Exception as e:
            logger.error(f"解密后的数据 msgpack 解析失败: {e}")
            return None
    except Exception as e:
        logger.error(f"文件解密失败 {infile.name}: {e}")
        return None


def decrypt_and_save(
    *,
    bin_file_path: Path,
    json_output_path: Path,
    region: str = "jp",
) -> dict[str, Any] | None:
    """解密 .bin 文件，将其保存为 .json，并返回解密后的字典。"""
    decrypted_data = decrypt_packet(bin_file_path, region)
    if decrypted_data is None:
        logger.error(f"文件解密失败: {bin_file_path.name}")
        return None

    logger.info(f"文件解密成功: {bin_file_path.name}")

    try:
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(
            json.dumps(decrypted_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"解密数据已保存到: {json_output_path}")
    except Exception as e:
        logger.error(f"保存解密后的 JSON 文件失败: {e}")
        # 即使保存失败，也继续返回数据

    return decrypted_data


def load_decrypted_json(json_file_path: Path) -> dict[str, Any] | None:
    """从文件加载已解密的 JSON 数据。"""
    try:
        return json.loads(json_file_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"加载 JSON 文件失败 {json_file_path.name}: {e}")
        return None
