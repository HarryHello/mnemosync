"""Shared utility functions for the core package."""


def last_user_message(messages: list[dict]) -> str:
    """Extract the content of the last user message from a message list."""
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""
