import asyncio
import aiohttp
import json
import os
from typing import Dict, List, Optional
from nonebot.log import logger

# --- 配置 ---
API_URL_TEMPLATE = "http://public-api.haruki.seiunx.com/alias/v1/music/{}"
REQUEST_TIMEOUT = 10
POLITE_DELAY = 0.1


async def _fetch_alias(session: aiohttp.ClientSession, song_id: int) -> Optional[List[str]]:
    """异步获取单个歌曲ID的别名。"""
    api_url = API_URL_TEMPLATE.format(song_id)
    try:
        async with session.get(api_url, timeout=REQUEST_TIMEOUT) as response:
            if response.status == 404:
                return None
            if response.status != 200:
                logger.debug(f"[get_aliases] API 请求失败 (Song ID: {song_id}), 状态码: {response.status}")
                return None

            data = await response.json()
            aliases: List[str] = []
            if isinstance(data, dict):
                if "data" in data and isinstance(data["data"], dict) and "aliases" in data["data"]:
                    aliases = data["data"]["aliases"]
                elif "aliases" in data:
                    aliases = data["aliases"]
            elif isinstance(data, list):
                aliases = data

            return aliases if aliases and isinstance(aliases, list) else None

    except asyncio.TimeoutError:
        logger.debug(f"[get_aliases] API 请求超时 (Song ID: {song_id})")
        return None
    except aiohttp.ClientError as e:
        logger.debug(f"[get_aliases] 网络错误 (Song ID: {song_id}): {e}")
        return None
    except Exception as e:
        logger.debug(f"[get_aliases] 解析响应失败 (Song ID: {song_id}): {e}")
        return None


async def fetch_all_aliases(guess_song_path: str, output_path: str) -> bool:
    """
    从 API 获取所有歌曲的别名并保存。

    :param guess_song_path: guess_song.json 的完整路径
    :param output_path: 输出 song_aliases.json 的完整路径
    :return: 是否成功
    """
    if not os.path.exists(guess_song_path):
        logger.error(f"[get_aliases] 找不到文件: {guess_song_path}")
        return False

    try:
        with open(guess_song_path, "r", encoding="utf-8") as f:
            song_data: List[Dict] = json.load(f)
        if not isinstance(song_data, list):
            logger.error("[get_aliases] guess_song.json 格式不正确。")
            return False
    except Exception as e:
        logger.error(f"[get_aliases] 读取 guess_song.json 失败: {e}")
        return False

    total_songs = len(song_data)
    logger.info(f"[get_aliases] 开始获取 {total_songs} 首歌曲的别名...")

    all_aliases: Dict[str, List[str]] = {}
    found_count = 0

    async with aiohttp.ClientSession() as session:
        for i, song in enumerate(song_data):
            song_id = song.get("id")
            if not song_id:
                continue

            aliases = await _fetch_alias(session, song_id)
            if aliases:
                all_aliases[str(song_id)] = aliases
                found_count += 1

            await asyncio.sleep(POLITE_DELAY)

            # 每 50 首打一次日志
            if (i + 1) % 50 == 0:
                logger.info(f"[get_aliases] 进度: {i + 1}/{total_songs}")

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_aliases, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[get_aliases] 保存失败: {e}")
        return False

    logger.info(f"[get_aliases] 完成。为 {found_count}/{total_songs} 首歌曲找到了别名，已保存到 {output_path}")
    return True


# --- 独立运行入口 ---
async def _main():
    guess_song_path = input("请输入 'guess_song.json' 文件的完整路径: \n> ").strip()
    await fetch_all_aliases(guess_song_path, "song_aliases.json")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except RuntimeError as e:
        if "Event loop is closed" in str(e) and os.name == 'nt':
            pass
        else:
            raise
