"""消息提取器实现."""

from typing import Any


def extract_latest_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从上下文列表中获取最新的一条用户消息.

    从后向前遍历，返回第一条 role="user" 的消息.

    Args:
        messages: OpenAI 格式的消息列表

    Returns:
        最新的一条用户消息，如果没有则返回 None

    Example:
        >>> messages = [
        ...     {"role": "user", "content": "你好"},
        ...     {"role": "assistant", "content": "你好呀"},
        ...     {"role": "user", "content": "新问题"},
        ... ]
        >>> extract_latest_user_message(messages)
        {"role": "user", "content": "新问题"}
    """
    # 从后向前遍历，找到第一条 user 消息
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg
    return None


def extract_all_user_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取所有用户消息.

    Args:
        messages: OpenAI 格式的消息列表

    Returns:
        所有 user 角色的消息列表
    """
    return [msg for msg in messages if msg.get("role") == "user"]
