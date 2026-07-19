"""提示词清洗 Agent 的 prompt 拼装.

模板位于:
- `src/core/agents/prompts/defaults/prompt_cleaning_system.md`
- `src/core/agents/prompts/defaults/prompt_cleaning_user.md`
由 PromptStore 加载. 用户覆盖: `data/prompts/prompt_cleaning_*.md`.

服务器优先 (server-first) 人格设计: 客户端 system 消息中的人格描述
应被丢弃, 仅保留功能性指令. 本 Agent 负责分离两者.
"""

from __future__ import annotations

from src.core.prompts import get_prompt_store


def load_prompt_cleaning_system() -> str:
    """加载清洗 Agent 的 system prompt (无占位符)."""
    return get_prompt_store().load("prompt_cleaning_system")


def build_prompt_cleaning_user_prompt(system_message: str) -> str:
    """构建清洗 Agent 的 user prompt.

    Args:
        system_message: 客户端发来的 system 消息内容

    Returns:
        填充后的用户 prompt
    """
    tmpl = get_prompt_store().load("prompt_cleaning_user")
    return tmpl.replace("__SYSTEM_MESSAGE__", system_message)
