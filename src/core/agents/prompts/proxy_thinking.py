"""Proxy thinking agent prompt.

模板位于 `src/core/agents/prompts/defaults/proxy_thinking.md`, 由 PromptStore 加载.
用户覆盖: `data/prompts/proxy_thinking.md`.
"""

from __future__ import annotations

from src.core.prompts import get_prompt_store


def build_proxy_thinking_prompt(
    user_name: str, relationship: str, memories: str, user_message: str
) -> str:
    tmpl = get_prompt_store().load("proxy_thinking")
    return (
        tmpl.replace("__USER_NAME__", user_name)
        .replace("__RELATIONSHIP__", relationship)
        .replace("__MEMORIES__", memories)
        .replace("__USER_MESSAGE__", user_message)
    )
