# plugins/buaa_msm/infra/decryptor.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from nonebot.log import logger

from .. import decrypt


def load_decrypted_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return decrypt.load_decrypted_json(path)
    except Exception as e:
        logger.error(f"load_decrypted_json 失败: {e}")
        return None


def decrypt_and_save(*, bin_file_path: Path, json_output_path: Path) -> Optional[Dict[str, Any]]:
    try:
        return decrypt.decrypt_and_save(bin_file_path=bin_file_path, json_output_path=json_output_path)
    except Exception as e:
        logger.error(f"decrypt_and_save 失败: {e}")
        return None
