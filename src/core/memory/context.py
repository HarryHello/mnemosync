"""记忆上下文拼装.

主对话 Agent 调用本模块拼装发给上游模型的完整上下文:
  system: 人格 prompt + 永久记忆 + 检索记忆 + 关系摘要
  user/assistant: 当前对话历史

框架文本 (行为准则/section 标题) 现在从 PromptStore 加载,
registry 名: `main_dialogue_frame`.
"""

from __future__ import annotations

from typing import Any

from src.core.memory.models import MemoryEntry, Relationship
from src.core.prompts import get_prompt_store


def format_permanent_memories(entries: list[MemoryEntry]) -> str:
    """格式化永久记忆列表为 prompt 文本."""
    if not entries:
        return "（暂无永久记忆）"
    lines = []
    for i, e in enumerate(entries, 1):
        tags = f" [{', '.join(e.emotional_tags)}]" if e.emotional_tags else ""
        lines.append(f"{i}. {e.content}{tags}")
    return "\n".join(lines)


def format_retrieved_memories(entries: list[MemoryEntry]) -> str:
    """格式化检索到的相关记忆."""
    if not entries:
        return "（暂无相关记忆）"
    lines = []
    for i, e in enumerate(entries, 1):
        tags = f" [{', '.join(e.emotional_tags)}]" if e.emotional_tags else ""
        lines.append(f"{i}. {e.content}{tags}")
    return "\n".join(lines)


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


def render_main_dialogue_system(
    persona_prompt: str,
    persona_name: str,
    user_name: str,
    permanent_memories: list[MemoryEntry],
    retrieved_memories: list[MemoryEntry],
    relationship: Relationship | None,
    proxy_thinking_result: str | None = None,
) -> str:
    """渲染主对话 system 段. 独立出来以便在装填时先算 token 预算 (见 short_term)."""
    frame = get_prompt_store().load("main_dialogue_frame")
    return (
        frame.replace("__PERSONA_NAME__", persona_name)
        .replace("__PERSONA_PROMPT__", persona_prompt)
        .replace("__USER_NAME__", user_name)
        .replace("__RELATIONSHIP__", format_relationship(relationship))
        .replace("__PERMANENT_MEMORIES__", format_permanent_memories(permanent_memories))
        .replace("__RETRIEVED_MEMORIES__", format_retrieved_memories(retrieved_memories))
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
) -> list[dict[str, Any]]:
    """拼装主对话 Agent 的完整 messages.

    Args:
        persona_prompt: 人格设定文本
        persona_name: 人格名称
        user_name: 当前用户名
        permanent_memories: 永久记忆列表
        retrieved_memories: 语义检索到的相关记忆
        relationship: 用户关系状态
        conversation_history: 当前对话历史 (跨前端流水, 已在 short_term 裁剪)
        proxy_thinking_result: 代理思考结果（可选）

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
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content}
    ]
    messages.extend(conversation_history)
    return messages
