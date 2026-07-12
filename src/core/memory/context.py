"""记忆上下文拼装.

主对话 Agent 调用本模块拼装发给上游模型的完整上下文:
  system: 人格 prompt + 永久记忆 + 检索记忆 + 关系摘要
  user/assistant: 当前对话历史
"""

from __future__ import annotations

from typing import Any

from src.core.memory.models import MemoryEntry, Relationship


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
        conversation_history: 当前对话历史（来自 checkpoint, OpenAI 格式）
        proxy_thinking_result: 代理思考结果（可选）

    Returns:
        OpenAI 格式的 messages 列表
    """
    system_parts: list[str] = []
    system_parts.append(f"你是 {persona_name}，以下是你的核心设定：\n\n{persona_prompt}")
    system_parts.append("---")
    system_parts.append("## 关于当前对话对象\n")
    system_parts.append(f"- 用户名：{user_name}")
    system_parts.append(f"- 你们的关系：{format_relationship(relationship)}")
    system_parts.append("---")
    system_parts.append(f"## 你对 {user_name} 的记忆\n")
    system_parts.append("### 永久记忆（你永远记得）")
    system_parts.append(format_permanent_memories(permanent_memories))
    system_parts.append("\n### 相关记忆（此时想起的）")
    system_parts.append(format_retrieved_memories(retrieved_memories))
    system_parts.append("---")
    system_parts.append("""## 行为准则

1. 自然地将对用户的了解融入对话，不要生硬地背诵记忆
2. 尊重隐私边界：不同用户之间的记忆不应混淆
3. 注意情绪：如果用户近期有负面情绪，适当表达关心
4. 保持性格一致：你的回复应符合""" + f" {persona_name} 的人设")
    system_parts.append('5. 不要提及"记忆系统"、"数据库"等系统内部概念')

    if proxy_thinking_result:
        system_parts.append("---")
        system_parts.append("## 思考辅助")
        system_parts.append(
            "以下是对用户消息的预先分析，供你参考——请自然地吸收这些理解，\n"
            "而不是逐条复述：\n\n"
            + proxy_thinking_result
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "\n".join(system_parts)}
    ]
    messages.extend(conversation_history)
    return messages
