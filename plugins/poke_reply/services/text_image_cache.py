# services/text_image_cache.py
import json
import time
import asyncio
from pathlib import Path
from typing import Dict, Optional, Tuple
from nonebot import on_message, logger, get_bot
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageEvent,
    Message,
    MessageSegment,
    Bot
)
from nonebot.rule import to_me
from nonebot.params import CommandArg
import hashlib

from ..config import PLUGIN_DIR, data_dir
from ..utils.common import download_and_hash_image

# 缓存文件路径
CACHE_FILE = data_dir / "text_image_cache.json"
# 缓存过期时间（10分钟）
CACHE_EXPIRE_TIME = 10 * 60

# 注册消息处理器 - 监听"转文字"回复
convert_to_text = on_message(rule=to_me(), priority=10, block=True)


class TextImageCache:
    def __init__(self):
        self.cache_file = CACHE_FILE
        self.cache_data: Dict[str, dict] = {}
        self.load_cache()

    def load_cache(self):
        """加载缓存数据"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache_data = json.load(f)
                logger.info(f"文本图片缓存加载成功，共 {len(self.cache_data)} 条记录")
            else:
                self.cache_data = {}
                self.save_cache()
        except Exception as e:
            logger.error(f"加载文本图片缓存失败: {e}")
            self.cache_data = {}

    def save_cache(self):
        """保存缓存数据"""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存文本图片缓存失败: {e}")

    def add_cache_by_image_hash(self, image_hash: str, group_id: int, original_text: str):
        """使用图片哈希添加缓存记录"""
        cache_key = f"{group_id}_{image_hash}"

        self.cache_data[cache_key] = {
            "image_hash": image_hash,
            "group_id": group_id,
            "original_text": original_text,
            "timestamp": time.time(),
            "expire_time": time.time() + CACHE_EXPIRE_TIME
        }

        self.save_cache()
        logger.info(f"已缓存文本图片记录: 图片哈希={image_hash}, 群组={group_id}")
        logger.info(f"缓存键: {cache_key}")
        logger.info(f"当前缓存总数: {len(self.cache_data)}")

    def get_cache_by_image_hash(self, image_hash: str, group_id: int) -> Optional[dict]:
        """使用图片哈希获取缓存记录"""
        cache_key = f"{group_id}_{image_hash}"

        # 清理过期缓存
        self.clean_expired_cache()

        record = self.cache_data.get(cache_key)
        logger.info(f"查找缓存记录: {cache_key}, 找到: {record is not None}")
        logger.info(f"当前所有缓存键: {list(self.cache_data.keys())}")

        return record

    def remove_cache_by_image_hash(self, image_hash: str, group_id: int) -> bool:
        """使用图片哈希移除缓存记录"""
        cache_key = f"{group_id}_{image_hash}"

        if cache_key in self.cache_data:
            del self.cache_data[cache_key]
            self.save_cache()
            logger.info(f"已移除缓存记录: 图片哈希={image_hash}, 群组={group_id}")
            return True
        return False

    def clean_expired_cache(self):
        """清理过期缓存"""
        current_time = time.time()
        expired_keys = []

        for key, record in self.cache_data.items():
            if record.get("expire_time", 0) < current_time:
                expired_keys.append(key)

        for key in expired_keys:
            del self.cache_data[key]

        if expired_keys:
            logger.info(f"清理了 {len(expired_keys)} 条过期缓存记录")
            self.save_cache()


# 全局缓存实例
text_image_cache = TextImageCache()


async def create_forward_message(bot: Bot, group_id: int, text: str) -> list:
    """
    创建合并转发消息

    Args:
        bot: 机器人实例
        group_id: 群组ID
        text: 要转发的文本内容

    Returns:
        合并转发消息节点列表
    """
    try:
        # 获取机器人信息
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "机器人")
        bot_uin = bot_info.get("user_id", bot.self_id)

        # 创建转发消息节点
        forward_nodes = [
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": str(bot_uin),
                    "content": text
                }
            }
        ]

        return forward_nodes

    except Exception as e:
        logger.error(f"创建合并转发消息失败: {e}")
        # 如果合并转发失败，返回普通文本消息格式
        return [
            {
                "type": "node",
                "data": {
                    "name": "转文字结果",
                    "uin": str(bot.self_id),
                    "content": f"【转文字结果】\n{text}"
                }
            }
        ]

@convert_to_text.handle()
async def handle_convert_to_text(bot: Bot, event: GroupMessageEvent):
    """处理'转文字'回复"""
    try:
        # 检查消息内容是否为"转文字"
        message_text = event.get_plaintext().strip()
        if message_text != "转文字":
            return

        # 检查是否是回复消息
        if not hasattr(event, 'reply') or event.reply is None:
            await convert_to_text.finish("请回复要转换的图片消息并说'转文字'喵！")
            return

        # 获取被回复的消息
        replied_message = event.reply
        group_id = event.group_id

        logger.info(f"收到转文字请求: 群组={group_id}, 回复消息ID={replied_message.message_id}")

        # 检查被回复的消息是否包含图片
        image_url = None
        for segment in replied_message.message:
            if segment.type == "image":
                image_url = segment.data.get("url", "")
                break

        if not image_url:
            await convert_to_text.finish("请回复一张由长文本转换的图片消息喵！")
            return

        logger.info(f"找到图片URL: {image_url}")

        # 下载图片并计算哈希值
        success, image_hash = await download_and_hash_image(image_url)
        if not success:
            await convert_to_text.finish("下载图片失败，无法计算哈希值喵！")
            return

        logger.info(f"计算图片哈希: {image_hash}")

        # 使用图片哈希查找缓存记录
        cache_record = text_image_cache.get_cache_by_image_hash(image_hash, group_id)

        if not cache_record:
            await convert_to_text.finish("未找到对应的文本缓存，可能已过期或不是由长文本转换的图片喵！")
            return

        # 获取原始文本
        original_text = cache_record.get("original_text", "")
        if not original_text:
            await convert_to_text.finish("缓存文本为空，无法转换喵！")
            return

        logger.info(f"找到缓存文本，长度: {len(original_text)}")

        # 创建合并转发消息
        forward_nodes = await create_forward_message(bot, group_id, original_text)

        # 发送合并转发消息
        await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)

        # 移除缓存记录
        text_image_cache.remove_cache_by_image_hash(image_hash, group_id)

        logger.info(f"成功将图片消息转换为文字并发送合并转发")

    except Exception as e:
        logger.error(f"处理转文字命令时出错: {e}")
        await convert_to_text.finish("转换失败，请稍后重试喵！")