import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import nonebot_plugin_localstore as store
from .config import load_config, HelpConfig
from .drawer import render_help_image

# 缓存文件名模板
CACHE_IMG_TEMPLATE = "help_cache_{mode}.png"
CACHE_HASH_NAME = "config_hash.txt"


def get_config_hash(config: HelpConfig) -> str:
    """计算配置文件的 MD5 哈希值"""
    config_str = json.dumps(config.dict(), sort_keys=True)
    return hashlib.md5(config_str.encode("utf-8")).hexdigest()


def extract_links(text_list: List[str]) -> List[str]:
    """提取文本列表中的 URL"""
    full_text = "\n".join(text_list)
    pattern = r'(https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*)'
    links = list(set(re.findall(pattern, full_text)))
    return sorted(links, key=full_text.find)


class HelpManager:
    def __init__(self):
        self.cache_dir = store.get_plugin_data_dir()
        self.hash_path = self.cache_dir / CACHE_HASH_NAME
        # 确保目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_config = load_config()

    def _save_cache(self, img_bytes: bytes, img_path: Path, new_hash: str):
        """保存图片和哈希值到 data 目录"""
        with open(img_path, "wb") as f:
            f.write(img_bytes)
        # 更新哈希值 (注意：哈希值是通用的，只要配置没变，日/夜缓存都有效)
        with open(self.hash_path, "w", encoding="utf-8") as f:
            f.write(new_hash)

    def _is_night_mode(self) -> bool:
        """判断是否为夜间模式 (18:00 - 06:00)"""
        current_hour = datetime.now().hour
        return current_hour >= 18 or current_hour < 6

    async def get_help_data(self, force_update: bool = False) -> Tuple[Path, List[str]]:
        """
        获取帮助数据 (Async)
        返回: (图片路径: Path, 链接列表: list[str])
        """
        # 1. 重新加载配置
        self.current_config = load_config()
        current_hash = get_config_hash(self.current_config)

        # 2. 确定当前模式 (Day/Night)
        is_dark = self._is_night_mode()
        mode_suffix = "night" if is_dark else "day"

        # 确定当前模式对应的缓存图片路径
        current_img_path = self.cache_dir / CACHE_IMG_TEMPLATE.format(mode=mode_suffix)

        # 3. 检查缓存是否有效
        # 有效条件：Hash文件存在且匹配 + 当前模式的图片文件存在 + 非强制更新
        cache_valid = False
        if self.hash_path.exists() and current_img_path.exists():
            try:
                with open(self.hash_path, "r", encoding="utf-8") as f:
                    saved_hash = f.read().strip()
                if saved_hash == current_hash and not force_update:
                    cache_valid = True
            except Exception:
                cache_valid = False

        # 4. 如果缓存无效，重新渲染
        if not cache_valid:
            print(f"Rendering help image for mode: {mode_suffix.upper()}...")
            # 传入 is_dark 参数
            img_bytes = await render_help_image(self.current_config, is_dark=is_dark)
            self._save_cache(img_bytes, current_img_path, current_hash)

        # 5. 提取链接
        links = extract_links(self.current_config.help_text)

        return current_img_path, links


# 实例化全局管理器
help_manager = HelpManager()