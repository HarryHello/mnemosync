"""Shared utility functions for the core package."""

from typing import Any


def last_user_message(messages: list[dict[str, Any]]) -> str:
    """Extract the content of the last user message from a message list.

    多模态消息 (content 为 OpenAI content-part 数组) 提取 text 部分。
    旧实现遇 list 直接返回空串: 带图请求的当前消息在短期装填重建
    (nonstream/stream 都以本函数结果决定是否追加) 时被整体丢弃,
    上游只剩 system (beta.17 实测)。
    """
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                texts = [
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                return "\n".join(t for t in texts if t)
            return content if isinstance(content, str) else ""
    return ""
