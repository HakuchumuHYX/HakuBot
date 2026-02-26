import asyncio
import aiohttp
import json
import os
from typing import Dict, List, Optional

# --- 配置 ---

# 别名 API 的 URL 模板
API_URL_TEMPLATE = "http://public-api.haruki.seiunx.com/alias/v1/music/{}"

# 最终输出的文件名
OUTPUT_FILE = "song_aliases.json"

# 每次请求的超时时间（秒）
REQUEST_TIMEOUT = 10

# 每次请求之间的礼貌延迟（秒），避免请求过于频繁
POLITE_DELAY = 0.1


# --- 脚本 ---

async def fetch_alias(session: aiohttp.ClientSession, song_id: int) -> Optional[List[str]]:
    """
    异步获取单个歌曲ID的别名。
    """
    api_url = API_URL_TEMPLATE.format(song_id)

    try:
        async with session.get(api_url, timeout=REQUEST_TIMEOUT) as response:
            # 404 表示 API 知道这个 ID，但它没有别名
            if response.status == 404:
                return None

            # 处理其他错误
            if response.status != 200:
                print(f"  [!] API 请求失败 (Song ID: {song_id}), 状态码: {response.status}")
                return None

            data = await response.json()

            # 灵活解析 API 可能返回的数据结构
            aliases: List[str] = []
            if isinstance(data, dict):
                if "data" in data and isinstance(data["data"], dict) and "aliases" in data["data"]:
                    aliases = data["data"]["aliases"]
                elif "aliases" in data:
                    aliases = data["aliases"]
            elif isinstance(data, list):
                aliases = data

            if aliases and isinstance(aliases, list):
                return aliases
            else:
                return None  # API 响应成功，但别名列表为空

    except asyncio.TimeoutError:
        print(f"  [!] API 请求超时 (Song ID: {song_id})")
        return None
    except aiohttp.ClientError as e:
        print(f"  [!] 网络错误 (Song ID: {song_id}): {e}")
        return None
    except Exception as e:
        print(f"  [!] 解析响应失败 (Song ID: {song_id}): {e}")
        return None


async def main():
    """
    主执行函数
    """
    print("--- 歌曲别名下载器 ---")

    # 1. 获取 guess_song.json 的路径
    guess_song_path = input("请输入 'guess_song.json' 文件的完整路径: \n> ").strip()

    if not os.path.exists(guess_song_path):
        print(f"错误：文件未找到: {guess_song_path}")
        return

    if not os.path.isfile(guess_song_path):
        print(f"错误：路径不是一个文件: {guess_song_path}")
        return

    # 2. 读取歌曲列表
    try:
        with open(guess_song_path, "r", encoding="utf-8") as f:
            song_data: List[Dict] = json.load(f)
        if not isinstance(song_data, list):
            print("错误：'guess_song.json' 的格式不正确，根元素应为列表。")
            return
    except Exception as e:
        print(f"读取或解析 'guess_song.json' 失败: {e}")
        return

    total_songs = len(song_data)
    print(f"成功加载 {total_songs} 首歌曲数据。")

    # 3. 遍历并请求 API
    all_aliases: Dict[str, List[str]] = {}
    found_count = 0

    print("\n开始从 API 获取别名 (这可能需要几分钟)...")

    async with aiohttp.ClientSession() as session:
        for i, song in enumerate(song_data):
            song_id = song.get("id")
            song_title = song.get("title", f"ID {song_id}")

            if not song_id:
                print(f"  [!] 警告：跳过第 {i + 1} 个条目，缺少 'id' 字段。")
                continue

            print(f"  ({i + 1}/{total_songs}) 正在处理: [ID: {song_id}] {song_title}")

            aliases = await fetch_alias(session, song_id)

            if aliases:
                all_aliases[str(song_id)] = aliases
                found_count += 1
                print(f"    -> 找到 {len(aliases)} 个别名: {', '.join(aliases[:3])}{'...' if len(aliases) > 3 else ''}")

            # 礼貌延迟
            await asyncio.sleep(POLITE_DELAY)

    # 4. 保存结果
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(all_aliases, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"\n保存到 {OUTPUT_FILE} 失败: {e}")
        return

    print("\n--- 任务完成 ---")
    print(f"总共处理了 {total_songs} 首歌曲。")
    print(f"为 {found_count} 首歌曲找到了别名。")
    print(f"所有数据已保存到: {os.path.abspath(OUTPUT_FILE)}")


if __name__ == "__main__":
    # 修复在 Windows 上运行 asyncio 的潜在问题
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "Event loop is closed" in str(e) and os.name == 'nt':
            pass  # 忽略 Windows 上的常见关闭错误
        else:
            raise