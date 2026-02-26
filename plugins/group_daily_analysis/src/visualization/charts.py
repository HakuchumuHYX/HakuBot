from typing import Dict, List, Any
from datetime import datetime
from collections import defaultdict
from ..models import ActivityVisualization

class ActivityVisualizer:
    def generate_activity_visualization(self, messages: list) -> ActivityVisualization:
        """从消息生成完整的活跃度数据"""
        hourly_activity = {h: 0 for h in range(24)}
        daily_activity = defaultdict(int)
        user_message_count = defaultdict(int)
        
        for msg in messages:
            # NoneBot/OneBot message 'time' is timestamp
            ts = msg.get("time", 0)
            if not ts:
                continue
                
            dt = datetime.fromtimestamp(ts)
            
            # 小时活跃度
            hourly_activity[dt.hour] += 1
            
            # 每日活跃度
            date_str = dt.strftime("%Y-%m-%d")
            daily_activity[date_str] += 1
            
            # 用户活跃度统计
            sender = msg.get("sender", {})
            user_id = str(sender.get("user_id", ""))
            if user_id:
                user_message_count[user_id] += 1
        
        # 计算高峰时段（消息数量 > 平均值的小时）
        avg_hourly = sum(hourly_activity.values()) / 24 if hourly_activity else 0
        peak_hours = [hour for hour, count in hourly_activity.items() if count > avg_hourly and count > 0]
        peak_hours.sort(key=lambda h: hourly_activity[h], reverse=True)
        
        # 生成用户活跃度排行
        user_activity_ranking = []
        for user_id, count in sorted(user_message_count.items(), key=lambda x: x[1], reverse=True):
            # 从消息中获取用户昵称
            user_name = "未知用户"
            for msg in messages:
                sender = msg.get("sender", {})
                if str(sender.get("user_id", "")) == user_id:
                    user_name = sender.get("card") or sender.get("nickname") or "未知用户"
                    break
            
            user_activity_ranking.append({
                "user_id": user_id,
                "user_name": user_name,
                "message_count": count
            })
                
        return ActivityVisualization(
            hourly_activity=hourly_activity,
            daily_activity=dict(daily_activity),
            user_activity_ranking=user_activity_ranking,
            peak_hours=peak_hours
        )

    def get_hourly_chart_data(self, hourly_activity: Dict[int, int]) -> Dict[str, Any]:
        """为Chart.js准备数据"""
        hours = list(range(24))
        counts = [hourly_activity.get(h, 0) for h in hours]
        
        # 找出最大值用于设置y轴范围
        max_count = max(counts) if counts else 10
        
        return {
            "labels": [f"{h:02d}:00" for h in hours],
            "data": counts,
            "max_y": int(max_count * 1.2)  # 留出一点顶部空间
        }
