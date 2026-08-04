"""Shared utility functions for the core package."""

from typing import Any


def last_user_message(messages: list[dict[str, Any]]) -> str:
    """Extract the content of the last user message from a message list."""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            return content if isinstance(content, str) else ""
    return ""
