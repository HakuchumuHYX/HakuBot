from difflib import SequenceMatcher
from typing import Dict, List, Tuple
from nonebot import logger

from utils.rendering.engine import render_markdown

from plugins.poke_reply.config import SIMILARITY_THRESHOLD
from plugins.poke_reply.models.data import data_manager
from plugins.poke_reply.utils.common import preprocess_text

# --- 文本相似度检查 ---


class SimilarityChecker:
    def __init__(self, threshold: float = SIMILARITY_THRESHOLD):
        self.threshold = threshold
        self.group_cache: Dict[int, List[str]] = {}

    def is_similar_to_group(self, group_id: int, new_text: str) -> bool:
        """检查新文本是否与指定群组的现有文本相似"""
        data_manager.ensure_group_data_loaded(group_id)
        if group_id not in data_manager.group_texts:
            return False

        existing_texts = data_manager.group_texts[group_id]
        processed_new = preprocess_text(new_text)

        for existing in existing_texts:
            processed_existing = preprocess_text(existing)
            similarity = SequenceMatcher(
                None, processed_new, processed_existing
            ).ratio()
            if similarity >= self.threshold:
                return True
        return False

    def calculate_similarity(self, text1: str, text2: str) -> float:
        processed1 = preprocess_text(text1)
        processed2 = preprocess_text(text2)
        return SequenceMatcher(None, processed1, processed2).ratio()

    def clear_group_cache(self, group_id: int):
        if group_id in self.group_cache:
            del self.group_cache[group_id]


similarity_checker = SimilarityChecker()

# --- 文本转图片 ---

# 模拟聊天气泡；markdown 允许内联 HTML，样式直接拼在正文后面
BUBBLE_CSS = """
<style>
    body {
        font-family: "Noto Sans CJK SC", sans-serif;
        background-color: #f2f2f2;
        padding: 20px;
    }
    .markdown-body {
        background-color: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        font-size: 16px;
        line-height: 1.6;
        color: #333;
    }
</style>
"""


async def convert_text_to_image(text: str, group_id: int) -> Tuple[bool, bytes]:
    """将文本转换为图片；失败时返回 (False, b"")，调用方改发纯文本"""
    try:
        # 行尾两个空格让 markdown 保留原文换行
        image_data = await render_markdown(
            text.replace("\n", "  \n") + BUBBLE_CSS, width=500
        )
        return True, image_data
    except Exception as e:
        logger.opt(exception=True).warning(f"Markdown 文本转图片失败: {e}")
        return False, b""
