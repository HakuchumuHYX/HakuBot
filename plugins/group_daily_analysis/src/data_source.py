from typing import List, Dict, Any
from datetime import datetime, timedelta
import json
from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger

from .config import plugin_config
from .database import db

class MessageFetcher:
    async def fetch_messages(self, bot: Bot, group_id: int) -> List[Dict[str, Any]]:
        """
        获取群消息历史 (从本地数据库)
        """
        try:
            # 1. 确定时间范围
            end_time = datetime.now()
            start_time = end_time - timedelta(days=plugin_config.analysis_days)
            start_ts = int(start_time.timestamp())
            end_ts = int(end_time.timestamp())

            logger.info(f"开始从数据库获取群 {group_id} 消息，时间范围: {start_time} - {end_time}")

            # 2. 从数据库获取
            rows = db.get_messages(str(group_id), start_ts, end_ts)
            
            valid_messages = []
            for row in rows:
                try:
                    # 尝试解析 raw_message 以还原完整结构
                    if row.get("raw_message"):
                        try:
                            message_chain = json.loads(row["raw_message"])
                        except json.JSONDecodeError:
                            message_chain = [{"type": "text", "data": {"text": row["content"]}}]
                    else:
                        message_chain = [{"type": "text", "data": {"text": row["content"]}}]

                    msg_obj = {
                        "message_id": row["id"], # 使用 DB ID 或原始 ID
                        "time": row["timestamp"],
                        "sender": {
                            "user_id": int(row["user_id"]) if row["user_id"].isdigit() else 0,
                            "nickname": row["sender_name"],
                            "card": row["sender_name"] # 暂用 nickname 代替 card
                        },
                        "message": message_chain,
                        "raw_message": row["content"] # 兼容性字段
                    }
                    valid_messages.append(msg_obj)
                except Exception as e:
                    continue

            logger.info(f"共获取到 {len(valid_messages)} 条有效消息")
            
            # 限制最大消息数 (如果配置 > 0)
            if plugin_config.max_messages > 0 and len(valid_messages) > plugin_config.max_messages:
                # 简单截断，保留最新的 N 条
                logger.warning(f"获取到的消息数({len(valid_messages)})超过 max_messages({plugin_config.max_messages})，将截断保留最新记录。建议在 config 中调大或设为 0 以防丢失数据。")
                valid_messages = valid_messages[-plugin_config.max_messages:]
                
            return valid_messages

        except Exception as e:
            logger.error(f"获取群消息失败: {e}")
            return []
