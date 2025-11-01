# core/similarity_check.py
import re
from difflib import SequenceMatcher
from typing import List, Dict
from nonebot import logger

from ..config import SIMILARITY_THRESHOLD
from .data_manager import data_manager
from ..utils.common import preprocess_text

class SimilarityChecker:
    def __init__(self, threshold: float = SIMILARITY_THRESHOLD):
        self.threshold = threshold
        self.group_cache: Dict[int, List[str]] = {}  # 按群组缓存处理后的文本

    def is_similar_to_group(self, group_id: int, new_text: str) -> bool:
        """
        检查新文本是否与指定群组的现有文本相似

        Args:
            group_id: 群组ID
            new_text: 新投稿的文本

        Returns:
            bool: 如果找到相似文本返回True，否则返回False
        """
        # 确保群组数据已加载
        data_manager.ensure_group_data_loaded(group_id)

        # 获取该群组的文本列表
        if group_id not in data_manager.group_texts:
            return False

        existing_texts = data_manager.group_texts[group_id]

        # 预处理新文本
        processed_new = preprocess_text(new_text)

        for existing in existing_texts:
            processed_existing = preprocess_text(existing)

            # 使用SequenceMatcher计算相似度
            similarity = SequenceMatcher(None, processed_new, processed_existing).ratio()

            # 如果相似度超过阈值，认为是相似文本
            if similarity >= self.threshold:
                return True

        return False

    def calculate_similarity(self, text1: str, text2: str) -> float:
        """计算两个文本的相似度"""
        processed1 = self.preprocess_text(text1)
        processed2 = self.preprocess_text(text2)
        return SequenceMatcher(None, processed1, processed2).ratio()

    def clear_group_cache(self, group_id: int):
        """清除指定群组的缓存"""
        if group_id in self.group_cache:
            del self.group_cache[group_id]


# 全局相似度检查器实例
similarity_checker = SimilarityChecker()