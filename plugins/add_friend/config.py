from typing import List, Set

# 自动同意好友请求的群聊列表
AUTO_APPROVE_GROUPS: Set[str] = {"254612419", "819157441"}  # 请替换为你的指定群聊号

# 群号提取正则表达式模式
GROUP_PATTERNS: List[str] = [
    r'群(\d+)',
    r'来自群.?(\d+)',
    r'群.?(\d+)',
    r'group.?(\d+)',
    r'群号.?(\d+)',
    r'(\d+)'
]

# 命令优先级
FRIEND_REQUEST_PRIORITY = 1
COMMAND_PRIORITY = 1

# 消息模板
WELCOME_MESSAGE = "欢迎好友！"
FRIEND_APPROVED_MESSAGE = "你好！现在我们是好友了。"
REJECT_MESSAGE = "您的验证信息错误！请检查验证信息中是否包含本群群号或本群群号是否在白名单中。若发现错误，请联系bot主"
REQUEST_NOTIFICATION_TEMPLATE = (
    "收到新的好友申请：\nQQ：{user_id}\n来自群：{group}\n验证信息：{comment}\n\n"
    "请使用以下命令处理：\n/同意好友 {user_id}\n/拒绝好友 {user_id}"
)

# 防重复处理配置
CACHE_EXPIRE_TIME = 300  # 5分钟