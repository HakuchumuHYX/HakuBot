"""
用户分析模块
负责统计用户活跃度、消息数量等
"""
from collections import defaultdict
from typing import Dict, List, Any
from nonebot.log import logger


class UserAnalyzer:
    """用户分析器"""
    
    def __init__(self):
        pass
    
    def analyze_users(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析用户活跃度
        
        Args:
            messages: 消息列表
            
        Returns:
            用户分析结果字典
        """
        user_stats = defaultdict(lambda: {
            "message_count": 0,
            "character_count": 0,
            "user_id": "",
            "nickname": "",
            "card": ""
        })
        
        for msg in messages:
            sender = msg.get("sender", {})
            user_id = str(sender.get("user_id", ""))
            
            if not user_id:
                continue
            
            # 初始化用户信息
            if user_stats[user_id]["message_count"] == 0:
                user_stats[user_id]["user_id"] = user_id
                user_stats[user_id]["nickname"] = sender.get("nickname", "")
                user_stats[user_id]["card"] = sender.get("card", "")
            
            # 统计消息数
            user_stats[user_id]["message_count"] += 1
            
            # 统计字符数
            for seg in msg.get("message", []):
                if seg.get("type") == "text":
                    text = seg.get("data", {}).get("text", "")
                    user_stats[user_id]["character_count"] += len(text)
        
        return dict(user_stats)
    
    def get_top_users(self, user_analysis: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取最活跃的用户列表
        
        Args:
            user_analysis: 用户分析结果
            limit: 返回数量限制
            
        Returns:
            活跃用户列表，按消息数量降序排序
        """
        # 转换为列表并排序
        user_list = []
        for user_id, stats in user_analysis.items():
            user_list.append({
                "user_id": user_id,
                "nickname": stats.get("nickname", ""),
                "card": stats.get("card", ""),
                "message_count": stats.get("message_count", 0),
                "character_count": stats.get("character_count", 0)
            })
        
        # 按消息数量降序排序
        user_list.sort(key=lambda x: x["message_count"], reverse=True)
        
        # 返回前 limit 个
        return user_list[:limit]
    
    def get_user_activity_ranking(self, user_analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        获取用户活跃度排行（完整列表）
        
        Args:
            user_analysis: 用户分析结果
            
        Returns:
            所有用户的活跃度排行
        """
        return self.get_top_users(user_analysis, limit=len(user_analysis))
