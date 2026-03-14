# file: decrypt.py
import msgpack
import json
from pathlib import Path
from typing import Dict, Any, Optional
from nonebot.log import logger

# 尝试导入 sssekai，如果失败则提供明确提示
try:
    from sssekai.crypto.APIManager import decrypt, SEKAI_APIMANAGER_KEYSETS

    SSSEKAI_LOADED = True
except ImportError:
    SSSEKAI_LOADED = False
    logger.error("=" * 50)
    logger.error("关键依赖 'sssekai' 未安装！")
    logger.error("请执行: pip install sssekai")
    logger.error("解密功能将无法使用。")
    logger.error("=" * 50)


def decrypt_packet(infile: Path, region='jp') -> Optional[Dict[str, Any]]:
    """
    解密 sssekai 加密的数据包 (bin) 并返回 Python 字典
    """
    if not SSSEKAI_LOADED:
        logger.error("decrypt_packet 调用失败: 'sssekai' 模块未加载。")
        return None

    try:
        data = infile.read_bytes()
        plain = decrypt(data, SEKAI_APIMANAGER_KEYSETS[region])

        try:
            msg = msgpack.unpackb(plain)
            return msg
        except Exception as e:
            logger.error(f"解密后的数据 'msgpack' 解析失败: {e}")
            return None

    except Exception as e:
        logger.error(f"Error decrypting packet {infile.name}: {e}")
        return None


def decrypt_and_save(bin_file_path: Path, json_output_path: Path, region='jp') -> Optional[Dict[str, Any]]:
    """
    解密 .bin 文件，将其保存为 .json，并返回解密后的字典
    """

    # 1. 解密
    decrypted_data = decrypt_packet(bin_file_path, region)

    if decrypted_data is None:
        logger.error(f"文件解密失败: {bin_file_path.name}")
        return None

    logger.info(f"文件解密成功: {bin_file_path.name}")

    # 2. 保存为 JSON
    try:
        # 确保输出目录存在
        json_output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(decrypted_data, f, ensure_ascii=False, indent=2)
        logger.info(f"解密数据已保存到: {json_output_path}")

    except Exception as e:
        logger.error(f"保存解密后的 JSON 文件失败: {e}")
        # 即使保存失败，也继续返回数据，让主流程可以继续

    # 3. 返回字典
    return decrypted_data


def load_decrypted_json(json_file_path: Path) -> Optional[Dict[str, Any]]:
    """
    从文件加载已解密的 JSON 数据
    """
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载 JSON 文件失败 {json_file_path.name}: {e}")
        return None