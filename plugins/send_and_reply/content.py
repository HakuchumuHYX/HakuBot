# context.py
# 共享的上下文存储，用于回复功能
import time

message_context = {}


def prune_message_context(max_age_seconds=259200, max_entries=500):
    """清理消息上下文：删除超过 max_age_seconds（默认72小时）的条目；
    若条目数仍超过 max_entries，则按 timestamp 淘汰最旧的"""
    now = time.time()

    # 删除过期条目
    expired_ids = [
        msg_id for msg_id, ctx in message_context.items()
        if now - ctx.get("timestamp", 0) > max_age_seconds
    ]
    for msg_id in expired_ids:
        del message_context[msg_id]

    # 若仍超过上限，按 timestamp 淘汰最旧的条目
    if len(message_context) > max_entries:
        sorted_ids = sorted(message_context, key=lambda mid: message_context[mid].get("timestamp", 0))
        for msg_id in sorted_ids[:len(message_context) - max_entries]:
            del message_context[msg_id]
