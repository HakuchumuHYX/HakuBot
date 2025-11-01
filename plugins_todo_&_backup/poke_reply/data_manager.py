# core/data_manager.py
import json
import os
import time
import uuid
from typing import List, Dict, Tuple
from pathlib import Path
from nonebot import logger

from ..config import get_group_text_path, get_group_image_dir, get_group_image_list_path, DEFAULT_TEXTS
from ..utils.common import get_group_id

class TextDataManager:
    def __init__(self):
        self.group_texts: Dict[int, List[str]] = {}  # 按群号存储文本列表
        self.group_images: Dict[int, List[str]] = {}  # 新增：按群号存储图片文件名列表
        self.last_modified: Dict[int, float] = {}  # 按群号存储最后修改时间

    def load_text_data(self, group_id: int) -> bool:
        """加载指定群组的文本数据"""
        text_file_path = get_group_text_path(group_id)

        if not text_file_path.exists():
            # 文件不存在，创建空的文本列表
            self.group_texts[group_id] = []
            # 保存空列表到文件
            return self.save_text_data(group_id)

        try:
            # 检查文件修改时间
            current_modified = os.path.getmtime(text_file_path)
            if (group_id in self.last_modified and
                    current_modified == self.last_modified[group_id]):
                return True  # 文件未修改，无需重新加载

            self.last_modified[group_id] = current_modified

            # 读取JSON文件
            with open(text_file_path, 'r', encoding='utf-8') as f:
                file_content = f.read().strip()

                if not file_content:
                    # 文件为空，初始化为空列表
                    self.group_texts[group_id] = []
                    return True

                loaded_data = json.loads(file_content)

            # 确保读取到的是列表
            if isinstance(loaded_data, list):
                self.group_texts[group_id] = loaded_data
                return True
            else:
                # 如果文件内容不是列表，记录错误并重置
                logger.error(f"群 {group_id} 的文本文件格式错误，重置为空列表")
                self.group_texts[group_id] = []
                return self.save_text_data(group_id)

        except json.JSONDecodeError as e:
            # JSON解析错误，记录错误并重置
            logger.error(f"群 {group_id} 的文本文件JSON解析错误: {e}，重置为空列表")
            self.group_texts[group_id] = []
            return self.save_text_data(group_id)
        except Exception as e:
            # 其他加载错误
            logger.error(f"群 {group_id} 的文本文件加载失败: {e}")
            return False

    def load_image_data(self, group_id: int) -> bool:
        """加载指定群组的图片数据"""
        image_list_path = get_group_image_list_path(group_id)

        if not image_list_path.exists():
            # 文件不存在，创建空的图片列表
            self.group_images[group_id] = []
            return self.save_image_data(group_id)

        try:
            with open(image_list_path, 'r', encoding='utf-8') as f:
                file_content = f.read().strip()
                if not file_content:
                    self.group_images[group_id] = []
                    return True

                loaded_data = json.loads(file_content)
                if isinstance(loaded_data, list):
                    # 加载图片列表后，检查实际文件是否存在
                    valid_images = []
                    image_dir = get_group_image_dir(group_id)

                    for filename in loaded_data:
                        image_path = image_dir / filename
                        if image_path.exists():
                            valid_images.append(filename)
                        else:
                            logger.warning(f"群 {group_id} 的图片文件不存在: {filename}")

                    # 如果列表中有不存在的文件，保存更新后的列表
                    if len(valid_images) != len(loaded_data):
                        self.group_images[group_id] = valid_images
                        self.save_image_data(group_id)
                        logger.info(
                            f"群 {group_id} 图片列表已清理，移除了 {len(loaded_data) - len(valid_images)} 个不存在的文件")
                    else:
                        self.group_images[group_id] = loaded_data

                    return True
                else:
                    self.group_images[group_id] = []
                    return self.save_image_data(group_id)

        except Exception as e:
            logger.error(f"群 {group_id} 的图片列表加载失败: {e}")
            self.group_images[group_id] = []
            return False

    def save_text_data(self, group_id: int) -> bool:
        """保存指定群组的文本数据到文件"""
        if group_id not in self.group_texts:
            self.group_texts[group_id] = []

        text_file_path = get_group_text_path(group_id)
        try:
            # 确保目录存在
            text_file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(text_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.group_texts[group_id], f, ensure_ascii=False, indent=2)

            # 更新修改时间
            if os.path.exists(text_file_path):
                self.last_modified[group_id] = os.path.getmtime(text_file_path)

            return True
        except Exception as e:
            logger.error(f"保存群 {group_id} 的文本文件失败: {e}")
            return False

    def save_image_data(self, group_id: int) -> bool:
        """保存指定群组的图片列表到文件"""
        if group_id not in self.group_images:
            self.group_images[group_id] = []

        image_list_path = get_group_image_list_path(group_id)
        try:
            image_list_path.parent.mkdir(parents=True, exist_ok=True)
            with open(image_list_path, 'w', encoding='utf-8') as f:
                json.dump(self.group_images[group_id], f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存群 {group_id} 的图片列表失败: {e}")
            return False

    def add_text(self, group_id: int, text: str) -> bool:
        """为指定群组添加新文本"""
        # 确保群组数据已加载
        if group_id not in self.group_texts:
            if not self.load_text_data(group_id):
                return False

        self.group_texts[group_id].append(text)
        return self.save_text_data(group_id)

    def add_image(self, group_id: int, image_data: bytes, file_extension: str) -> Tuple[bool, str]:
        """为指定群组添加新图片"""
        # 确保图片数据已加载
        if group_id not in self.group_images:
            if not self.load_image_data(group_id):
                return False, ""

        # 生成唯一文件名
        filename = f"{uuid.uuid4().hex}.{file_extension}"
        image_dir = get_group_image_dir(group_id)
        image_path = image_dir / filename

        try:
            # 保存图片文件
            with open(image_path, 'wb') as f:
                f.write(image_data)

            # 添加到图片列表
            self.group_images[group_id].append(filename)
            if self.save_image_data(group_id):
                return True, filename
            else:
                # 如果保存列表失败，删除图片文件
                if image_path.exists():
                    image_path.unlink()
                return False, ""
        except Exception as e:
            logger.error(f"保存图片文件失败: {e}")
            return False, ""

    def get_random_text(self, group_id: int) -> str:
        """获取指定群组的随机文本"""
        # 确保群组数据已加载
        if group_id not in self.group_texts:
            if not self.load_text_data(group_id):
                return "数据加载失败，请联系管理员喵！"

        if (not self.group_texts[group_id] or
                not self.is_text_list_valid(group_id)):
            return "这个群还没有投稿内容喵，快来投稿吧！"

        import random
        return random.choice(self.group_texts[group_id])

    def get_random_image_path(self, group_id: int) -> str:
        """获取指定群组的随机图片路径"""
        if group_id not in self.group_images:
            if not self.load_image_data(group_id):
                return ""

        if not self.group_images[group_id]:
            return ""

        import random
        image_dir = get_group_image_dir(group_id)
        filename = random.choice(self.group_images[group_id])
        image_path = image_dir / filename

        if image_path.exists():
            return str(image_path)
        else:
            # 如果文件不存在，从列表中移除
            self.group_images[group_id].remove(filename)
            self.save_image_data(group_id)
            logger.info(f"群 {group_id} 的图片文件不存在，已从列表中移除: {filename}")
            return ""

    def get_content_weights(self, group_id: int) -> Tuple[int, int]:
        """获取文本和图片的权重（数量）"""
        text_count = self.get_text_count(group_id)
        image_count = self.get_image_count(group_id)
        return text_count, image_count

    def get_text_count(self, group_id: int) -> int:
        """获取指定群组的文本数量"""
        if group_id not in self.group_texts:
            return 0
        return len(self.group_texts[group_id])

    def get_image_count(self, group_id: int) -> int:
        """获取指定群组的图片数量"""
        if group_id not in self.group_images:
            return 0
        return len(self.group_images[group_id])

    def is_text_list_valid(self, group_id: int) -> bool:
        """检查指定群组的文本列表是否有效（不是错误状态）"""
        return (group_id in self.group_texts and
                isinstance(self.group_texts[group_id], list) and
                (not self.group_texts[group_id] or
                 self.group_texts[group_id][0] not in DEFAULT_TEXTS))

    def ensure_group_data_loaded(self, group_id: int) -> bool:
        """确保群组数据已加载，返回是否成功"""
        if group_id not in self.group_texts:
            if not self.load_text_data(group_id):
                return False
        if group_id not in self.group_images:
            if not self.load_image_data(group_id):
                return False
        return True

    def get_all_group_ids(self) -> List[int]:
        """获取所有有数据的群组ID"""
        return list(self.group_texts.keys())

    def cleanup_missing_images(self, group_id: int) -> int:
        """清理指定群组中不存在的图片文件，返回清理的数量"""
        if group_id not in self.group_images:
            return 0

        initial_count = len(self.group_images[group_id])
        image_dir = get_group_image_dir(group_id)
        valid_images = []

        for filename in self.group_images[group_id]:
            image_path = image_dir / filename
            if image_path.exists():
                valid_images.append(filename)

        removed_count = initial_count - len(valid_images)
        if removed_count > 0:
            self.group_images[group_id] = valid_images
            self.save_image_data(group_id)
            logger.info(f"群 {group_id} 清理了 {removed_count} 个不存在的图片文件")

        return removed_count


# 全局数据管理器实例
data_manager = TextDataManager()