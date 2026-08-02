"""记忆上下文拼装.

主对话 Agent 调用本模块拼装发给上游模型的完整上下文:
  system: 人格 prompt + 永久记忆 + 检索记忆 + 关系摘要
  user/assistant: 当前对话历史

框架文本 (行为准则/section 标题) 现在从 PromptStore 加载,
registry 名: `main_dialogue_frame`.
"""

from __future__ import annotations

from typing import Any

from src.core.memory.models import MemoryEntry, Relationship, Visibility
from src.core.memory.trigger_reason import (
    TriggerReason,
    format_trigger_reason,
)
from src.core.prompts import get_prompt_store


def _memory_scope(entry: MemoryEntry, channel_type: str | None) -> str:
    """给模型保留受众语义，避免在群聊中主动披露私有记忆."""
    if entry.visibility == Visibility.PUBLIC:
        return "公开"
    if entry.visibility == Visibility.SOURCE_RESTRICTED:
        if channel_type == "group":
            return "当前发言者私有；群聊中勿主动披露"
        return "当前发言者私有"
    if entry.space_id:
        return "当前空间共享"
    return "受限可见；谨慎使用"


def _format_memories(
    entries: list[MemoryEntry],
    empty_text: str,
    channel_type: str | None,
) -> str:
    if not entries:
        return empty_text
    lines = []
    for i, entry in enumerate(entries, 1):
        tags = f" [{', '.join(entry.emotional_tags)}]" if entry.emotional_tags else ""
        lines.append(
            f"{i}. [{_memory_scope(entry, channel_type)}] {entry.content}{tags}"
        )
    return "\n".join(lines)


def format_permanent_memories(
    entries: list[MemoryEntry], channel_type: str | None = None,
) -> str:
    """格式化永久记忆，并保留模型需要的受众语义."""
    return _format_memories(entries, "（暂无永久记忆）", channel_type)


def format_retrieved_memories(
    entries: list[MemoryEntry], channel_type: str | None = None,
) -> str:
    """格式化检索记忆，并保留模型需要的受众语义."""
    return _format_memories(entries, "（暂无相关记忆）", channel_type)


def format_relationship(rel: Relationship | None) -> str:
    """格式化关系状态为 prompt 文本."""
    if rel is None:
        return "新用户（尚未建立关系）"
    return (
        f"关系类型: {rel.type}（亲密度 {rel.intimacy_score:.2f}/1.0, "
        f"信任度 {rel.trust_level:.2f}/1.0, 互动 {rel.interaction_count} 次）"
        + (f"\n备注: {rel.notes}" if rel.notes else "")
    )


def _proxy_thinking_section(proxy_thinking_result: str | None) -> str:
    """当 proxy_thinking 有内容时, 生成完整段落; 否则空串.

    段落自带前导 '\\n---\\n', 直接拼在模板末尾即可.
    """
    if not proxy_thinking_result:
        return ""
    return (
        "\n---\n"
        "## 思考辅助\n"
        "以下是对用户消息的预先分析，供你参考——请自然地吸收这些理解，\n"
        "而不是逐条复述：\n\n"
        + proxy_thinking_result
    )


def _channel_label(channel_type: str | None) -> str:
    if channel_type == "group":
        return "群聊"
    if channel_type == "direct":
        return "私聊"
    return "未标明"


def _speaker_label(current_speaker: str | None) -> str:
    return current_speaker.strip() if current_speaker and current_speaker.strip() else "未知参与者"


def _participants_label(
    active_participants: list[str] | None,
    current_speaker: str,
) -> str:
    participants = [name.strip() for name in active_participants or [] if name.strip()]
    if current_speaker != "未知参与者" and not any(
        name.casefold() == current_speaker.casefold() for name in participants
    ):
        participants.append(current_speaker)
    return "、".join(participants) if participants else "暂无可识别参与者"


def _tool_capability_hint(tools: list[dict[str, Any]] | None) -> str:
    """生成平台能力提示段.

    当本轮提供工具时, 告知模型工具的存在及其使用边界;
    不假设未提供的工具存在, 也不把可用工具列表当作必须使用.
    """
    if not tools:
        return ""
    tool_names = []
    for tool in tools:
        func = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = func.get("name", "") if isinstance(func, dict) else ""
        if name:
            tool_names.append(name)
    if not tool_names:
        return ""
    return (
        f"本轮可用工具: {', '.join(tool_names)}。\n"
        "这些工具表示你当前确实可以执行的动作，不代表必须使用。"
        "当一个轻量动作比文字回复更自然时，可以选择工具调用；"
        "但在调用前先确认目标是否合适。不得假设或调用未提供的动作。"
    )


def _build_persona_section(
    persona_definition: Any | None,
    persona_prompt: str,
    persona_name: str,
    space_id: str | None = None,
) -> str:
    """构建人格设定段.

    优先使用结构化 PersonaDefinition (含按空间覆盖);
    无结构化定义时回退到 legacy persona_prompt 文本.
    """
    if persona_definition is not None:
        from src.core.persona.definition import PersonaDefinition
        if isinstance(persona_definition, PersonaDefinition):
            identity = persona_definition.get_identity_for_space(space_id)
            return persona_definition.to_legacy_prompt(identity=identity)
    return persona_prompt


def _format_lorebook(
    lorebook_entries: list[Any] | None, query: str = "",
) -> str:
    """格式化 Lorebook 条目."""
    if not lorebook_entries:
        return ""
    parts = ["以下是你可能记得的固定知识（来自作者设定）："]
    for entry in lorebook_entries[:5]:
        parts.append(f"- {entry.content}")
    return "\n".join(parts) if len(parts) > 1 else ""


def render_main_dialogue_system(
    persona_prompt: str,
    persona_name: str,
    user_name: str,
    permanent_memories: list[MemoryEntry],
    retrieved_memories: list[MemoryEntry],
    relationship: Relationship | None,
    proxy_thinking_result: str | None = None,
    *,
    current_speaker: str | None = None,
    channel_type: str | None = None,
    space_label: str | None = None,
    active_participants: list[str] | None = None,
    trigger_reason: TriggerReason | None = None,
    tools: list[dict[str, Any]] | None = None,
    persona_definition: Any | None = None,
    space_id: str | None = None,
    lorebook_entries: list[Any] | None = None,
) -> str:
    """渲染主对话 system 段；user_name 仅保留为旧调用方的显示名兜底."""
    frame = get_prompt_store().load("main_dialogue_frame")
    speaker = _speaker_label(current_speaker or user_name)
    reason = trigger_reason or TriggerReason(
        reason="normal",
        description="常规发言，自然回应。",
    )
    persona_section = _build_persona_section(
        persona_definition, persona_prompt, persona_name, space_id=space_id,
    )
    lorebook_section = _format_lorebook(lorebook_entries)
    return (
        frame.replace("__PERSONA_NAME__", persona_name)
        .replace("__PERSONA_SECTION__", persona_section)
        .replace("__CURRENT_SPEAKER__", speaker)
        .replace("__USER_NAME__", speaker)
        .replace("__CHANNEL_TYPE__", _channel_label(channel_type))
        .replace("__SPACE_LABEL__", space_label or "无独立空间")
        .replace("__ACTIVE_PARTICIPANTS__", _participants_label(active_participants, speaker))
        .replace("__TRIGGER_REASON__", format_trigger_reason(reason))
        .replace("__TOOL_CAPABILITY_HINT__", _tool_capability_hint(tools))
        .replace("__RELATIONSHIP__", format_relationship(relationship))
        .replace(
            "__PERMANENT_MEMORIES__",
            format_permanent_memories(permanent_memories, channel_type),
        )
        .replace(
            "__RETRIEVED_MEMORIES__",
            format_retrieved_memories(retrieved_memories, channel_type),
        )
        .replace("__LOREBOK_ENTRIES__", lorebook_section)
        .replace("__PROXY_THINKING_SECTION__", _proxy_thinking_section(proxy_thinking_result))
    )


def build_main_dialogue_messages(
    persona_prompt: str,
    persona_name: str,
    user_name: str,
    permanent_memories: list[MemoryEntry],
    retrieved_memories: list[MemoryEntry],
    relationship: Relationship | None,
    conversation_history: list[dict[str, Any]],
    proxy_thinking_result: str | None = None,
    *,
    current_speaker: str | None = None,
    channel_type: str | None = None,
    space_label: str | None = None,
    active_participants: list[str] | None = None,
    trigger_reason: TriggerReason | None = None,
    tools: list[dict[str, Any]] | None = None,
    persona_definition: Any | None = None,
    space_id: str | None = None,
    lorebook_entries: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """拼装主对话 Agent 的完整 messages.

    Args:
        persona_prompt: 人格设定文本 (legacy, 无结构化定义时回退)
        persona_name: 人格名称
        user_name: 当前用户名
        permanent_memories: 永久记忆列表
        retrieved_memories: 语义检索到的相关记忆
        relationship: 用户关系状态
        conversation_history: 当前对话历史 (跨前端流水, 已在 short_term 裁剪)
        proxy_thinking_result: 代理思考结果（可选）
        current_speaker: 模型可读的当前发言者身份
        channel_type: direct / group / None
        space_label: 模型可读的空间名称
        active_participants: 裁剪后历史中的活跃参与者
        persona_definition: 结构化人格定义 (可选, 优先使用)
        space_id: 当前空间 ID (用于 persona space_overrides)
        lorebook_entries: Lorebook 预定义知识列表 (可选)

    Returns:
        OpenAI 格式的 messages 列表
    """
    system_content = render_main_dialogue_system(
        persona_prompt=persona_prompt,
        persona_name=persona_name,
        user_name=user_name,
        permanent_memories=permanent_memories,
        retrieved_memories=retrieved_memories,
        relationship=relationship,
        proxy_thinking_result=proxy_thinking_result,
        current_speaker=current_speaker,
        channel_type=channel_type,
        space_label=space_label,
        active_participants=active_participants,
        trigger_reason=trigger_reason,
        tools=tools,
        persona_definition=persona_definition,
        space_id=space_id,
        lorebook_entries=lorebook_entries,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content}
    ]
    messages.extend(conversation_history)
    return messages
