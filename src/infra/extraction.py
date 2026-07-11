"""消息提取: 协议适配层.

OpenAI API 的 messages 是上下文载体, 前端常把历史一起发来.
本模块从中分离出真正的新内容, 属于基础设施, 非 Agent.

精确匹配（不使用 embedding）: 语义层面的去重/关联由记忆分析 Agent 负责.
"""

from __future__ import annotations

from typing import Any, Protocol


class HistoryProvider(Protocol):
    """消息历史提供者（用于匹配已存储消息）."""

    async def get_history(self, source_user: str) -> list[dict[str, Any]]: ...


def extract_latest_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """提取最新一条 user 消息."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg
    return None


def extract_all_user_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取所有 user 消息."""
    return [m for m in messages if m.get("role") == "user"]


def _messages_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """精确匹配两条消息（role + content + name）."""
    return (
        a.get("role") == b.get("role")
        and a.get("content") == b.get("content")
        and a.get("name", "") == b.get("name", "")
    )


def extract_new_messages(
    messages: list[dict[str, Any]],
    server_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从前端 messages 中提取新增消息.

    按顺序遍历, 与 server_history 逐一匹配, 未匹配的为新增内容.

    Args:
        messages: 前端传来的完整消息列表
        server_history: 服务器已存储的历史消息（仅 role/content/name 字段）

    Returns:
        新增消息列表（messages 末尾未被历史匹配的部分）
    """
    # 找到最长历史前缀匹配
    new_messages: list[dict[str, Any]] = []
    history_index = 0

    for msg in messages:
        matched = False
        while history_index < len(server_history):
            hist = server_history[history_index]
            if _messages_equal(msg, hist):
                history_index += 1
                matched = True
                break
            history_index += 1
        if not matched:
            new_messages.append(msg)

    return new_messages
