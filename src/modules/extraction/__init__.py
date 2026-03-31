"""消息提取模块.

从前端请求的上下文中提取新增对话内容.

当前实现：提取最新的一条用户消息 (最简单策略).
"""

from .extractor import extract_latest_user_message, extract_all_user_messages

__all__ = [
    "extract_latest_user_message",
    "extract_all_user_messages",
]
