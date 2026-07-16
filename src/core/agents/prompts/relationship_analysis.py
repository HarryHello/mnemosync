"""Relationship analysis agent prompt.

模板位于 `src/core/agents/prompts/defaults/relationship_analysis.md`,
由 PromptStore 加载. 用户覆盖: `data/prompts/relationship_analysis.md`.
"""

from __future__ import annotations

from src.core.prompts import get_prompt_store


def build_relationship_analysis_prompt(
    current_relationship: str, conversation: str
) -> str:
    tmpl = get_prompt_store().load("relationship_analysis")
    return (
        tmpl.replace("__CURRENT_REL__", current_relationship)
        .replace("__CONVERSATION__", conversation)
    )
