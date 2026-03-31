"""上下文合并模块.

将历史记忆与当前消息合并，生成发送给上游模型的完整上下文.
"""

from typing import Any


def merge_context(
    memories: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """合并记忆、人格和当前消息.

    Args:
        memories: 从记忆池加载的历史记忆
        messages: 前端发送的当前消息
        system_prompt: 人格提示词 (可选)

    Returns:
        合并后的完整消息列表

    Example:
        >>> memories = [
        ...     {"role": "user", "content": "我叫马达"},
        ...     {"role": "assistant", "content": "你好马达"},
        ... ]
        >>> messages = [
        ...     {"role": "user", "content": "新问题"},
        ... ]
        >>> merge_context(memories, messages)
        [
            {"role": "user", "content": "我叫马达"},
            {"role": "assistant", "content": "你好马达"},
            {"role": "user", "content": "新问题"},
        ]
    """
    result = []

    # 1. 添加人格提示词 (system prompt)
    if system_prompt:
        result.append({"role": "system", "content": system_prompt})

    # 2. 添加历史记忆
    result.extend(memories)

    # 3. 添加当前消息
    result.extend(messages)

    return result


def deduplicate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去除重复的消息.

    从后向前遍历，保留每条唯一消息的最后一次出现.

    Args:
        messages: 消息列表

    Returns:
        去重后的消息列表 (保持原始顺序)
    """
    seen = set()
    result = []

    # 从后向前遍历，保留最后一次出现
    for msg in reversed(messages):
        # 创建消息的哈希标识
        msg_key = (msg.get("role"), msg.get("content"))

        if msg_key not in seen:
            seen.add(msg_key)
            result.append(msg)

    # 反转回原始顺序
    result.reverse()
    return result


def sort_messages_by_time(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按时间戳排序消息.

    Args:
        messages: 消息列表

    Returns:
        按时间升序排序的消息列表
    """
    # 当前简化实现：保持原始顺序
    # TODO: 解析时间戳并排序
    return messages
