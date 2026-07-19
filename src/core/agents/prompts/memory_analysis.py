"""记忆分析 Agent 的 prompt 拼装.

模板文件位于 `src/core/agents/prompts/defaults/memory_analysis.md`
(用户覆盖: `data/prompts/memory_analysis.md`), 由 PromptStore 加载.
"""

from __future__ import annotations

from src.core.prompts import get_prompt_store


def build_memory_analysis_prompt(
    source_user: str,
    conversation: str,
    decay_targets_section: str = "",
    *,
    persona_name: str,
    persona_addressing: str,
    user_addressing: str,
    relation_context: str,
) -> str:
    """构建记忆分析 Agent 的完整 prompt.

    避免 str.format() 被 JSON 示例中的括号干扰, 统一 __X__ + .replace 约定.
    persona_name / persona_addressing / user_addressing / relation_context 用于让
    Agent 用正确称谓提取记忆 (见 v0.2.9 [relation] 设计).
    """
    tmpl = get_prompt_store().load("memory_analysis")
    return (
        tmpl.replace("__SOURCE_USER__", source_user)
        .replace("__CONVERSATION__", conversation)
        .replace("__DECAY_TARGETS__", decay_targets_section)
        .replace("__PERSONA_NAME__", persona_name)
        .replace("__PERSONA_ADDRESSING__", persona_addressing)
        .replace("__USER_ADDRESSING__", user_addressing)
        .replace("__RELATION_CONTEXT__", relation_context)
    )


def load_decay_targets_header() -> str:
    """加载 '待评估记忆' 段头 (无占位符)."""
    return get_prompt_store().load("memory_analysis_decay_header")
