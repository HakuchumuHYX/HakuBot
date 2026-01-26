from typing import Dict, List, Any
from ..models import ActivityVisualization

class ActivityVisualizer:
    def generate_activity_visualization(self, messages: list) -> ActivityVisualization:
        """从消息生成活跃度数据"""
        from datetime import datetime
        
        hourly_activity = {h: 0 for h in range(24)}
        
        for msg in messages:
            # NoneBot/OneBot message 'time' is timestamp
            ts = msg.get("time", 0)
            if ts:
                dt = datetime.fromtimestamp(ts)
                hourly_activity[dt.hour] += 1
                
        return ActivityVisualization(hourly_activity=hourly_activity)

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
