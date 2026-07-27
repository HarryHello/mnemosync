"""Relationship analysis agent prompt builder.

模板位于 `src/core/agents/prompts/defaults/relationship_analysis.md`,
由 PromptStore 加载. 用户覆盖: `data/prompts/relationship_analysis.md`.

v0.2.12: emotion_analysis 由 graph 层预计算, 取代 Agent 自行调用 emotion_analyzer.
"""

from __future__ import annotations

from src.core.prompts import get_prompt_store


def build_relationship_analysis_prompt(
    current_relationship: str,
    conversation: str,
    *,
    persona_name: str,
    persona_addressing: str,
    user_addressing: str,
    relation_context: str,
    emotion_analysis: str = "",
    current_speaker: str = "未知参与者",
    channel_type: str | None = None,
) -> str:
    """构建关系分析 Agent 的 prompt.

    persona_name / persona_addressing / user_addressing / relation_context 用于让
    Agent 用正确的关系基线判断亲密/距离信号 (见 v0.2.9 [relation] 设计).
    emotion_analysis 由 graph 层预计算, 取代 Agent 自行调用 emotion_analyzer.
    """
    tmpl = get_prompt_store().load("relationship_analysis")
    channel_label = "群聊" if channel_type == "group" else "私聊" if channel_type == "direct" else "未标明"
    return (
        tmpl.replace("__CURRENT_REL__", current_relationship)
        .replace("__CURRENT_SPEAKER__", current_speaker)
        .replace("__CHANNEL_TYPE__", channel_label)
        .replace("__CONVERSATION__", conversation)
        .replace("__EMOTION_ANALYSIS__", emotion_analysis)
        .replace("__PERSONA_NAME__", persona_name)
        .replace("__PERSONA_ADDRESSING__", persona_addressing)
        .replace("__USER_ADDRESSING__", user_addressing)
        .replace("__RELATION_CONTEXT__", relation_context)
    )
