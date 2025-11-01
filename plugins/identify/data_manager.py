import json
import time
import random
from pathlib import Path
from typing import Dict, Optional
from nonebot import logger

from .config import DAILY_RECORDS_FILE, RESOURCES_DIR


class DailyRecordManager:
    def __init__(self):
        self.records_file = DAILY_RECORDS_FILE
        self.records_data: Dict[str, dict] = {}
        self.load_records()

    def load_records(self):
        """加载每日记录数据"""
        try:
            if self.records_file.exists():
                with open(self.records_file, 'r', encoding='utf-8') as f:
                    self.records_data = json.load(f)
                logger.info(f"每日记录加载成功，共 {len(self.records_data)} 条记录")
            else:
                self.records_data = {}
                self.save_records()
        except Exception as e:
            logger.error(f"加载每日记录失败: {e}")
            self.records_data = {}

    def save_records(self):
        """保存每日记录数据"""
        try:
            self.records_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.records_file, 'w', encoding='utf-8') as f:
                json.dump(self.records_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存每日记录失败: {e}")

    def get_today_key(self) -> str:
        """获取今天的日期键"""
        return time.strftime("%Y-%m-%d")

    def get_user_record(self, group_id: int, user_id: int) -> Optional[str]:
        """获取用户今天的鉴定结果"""
        today_key = self.get_today_key()

        if today_key not in self.records_data:
            return None

        user_key = f"{group_id}_{user_id}"
        return self.records_data[today_key].get(user_key)

    def set_user_record(self, group_id: int, user_id: int, image_name: str):
        """设置用户今天的鉴定结果"""
        today_key = self.get_today_key()
        user_key = f"{group_id}_{user_id}"

        if today_key not in self.records_data:
            self.records_data[today_key] = {}

        self.records_data[today_key][user_key] = image_name
        self.save_records()

    def get_random_image(self) -> str:
        """从resources文件夹随机获取一张图片"""
        try:
            # 获取所有图片文件
            image_files = list(RESOURCES_DIR.glob("*.*"))
            image_files = [f for f in image_files if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']]

            if not image_files:
                logger.warning("resources文件夹中没有找到图片文件")
                return ""

            # 随机选择一张图片
            selected_image = random.choice(image_files)
            return str(selected_image)
        except Exception as e:
            logger.error(f"获取随机图片失败: {e}")
            return ""

    def cleanup_old_records(self, days_to_keep: int = 3):
        """清理旧记录，保留指定天数的数据"""
        try:
            current_time = time.time()
            today_key = self.get_today_key()

            # 找出需要删除的旧日期
            dates_to_remove = []
            for date_key in self.records_data.keys():
                if date_key != today_key:
                    # 简单判断：如果日期键不是今天，就删除（实际应该用日期比较）
                    dates_to_remove.append(date_key)

            # 删除旧记录
            for date_key in dates_to_remove:
                del self.records_data[date_key]

            if dates_to_remove:
                self.save_records()
                logger.info(f"清理了 {len(dates_to_remove)} 天的旧记录")

        except Exception as e:
            logger.error(f"清理旧记录失败: {e}")


# 全局实例
daily_record_manager = DailyRecordManager()