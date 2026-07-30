"""主对话提示词的多用户/群聊语义测试."""

from __future__ import annotations

from src.core.memory.context import render_main_dialogue_system
from src.core.memory.models import MemoryEntry, MemoryType, Visibility


def _memory(
    content: str,
    *,
    visibility: Visibility = Visibility.SOURCE_RESTRICTED,
    space_id: str | None = None,
) -> MemoryEntry:
    entry = MemoryEntry.create(
        content=content,
        role="user",
        source_user="internal-user-id",
        memory_type=MemoryType.PERMANENT,
    )
    entry.visibility = visibility
    entry.space_id = space_id
    return entry


def test_group_prompt_uses_readable_speaker_and_participants() -> None:
    system = render_main_dialogue_system(
        persona_prompt="保持冷静",
        persona_name="绫音",
        user_name="internal-group-uuid",
        current_speaker="马达 | astrbot 486394990",
        channel_type="group",
        space_label="测试群",
        active_participants=[
            "Harry | astrbot 1914089741",
            "马达 | astrbot 486394990",
        ],
        permanent_memories=[],
        retrieved_memories=[],
        relationship=None,
    )

    assert "当前发言者：马达 | astrbot 486394990" in system
    assert "会话类型：群聊" in system
    assert "当前空间：测试群" in system
    assert "Harry | astrbot 1914089741、马达 | astrbot 486394990" in system
    assert "此关系只属于当前发言者" in system
    assert "internal-group-uuid" not in system
    assert "__" not in system


def test_group_prompt_marks_private_and_shared_memory_scope() -> None:
    system = render_main_dialogue_system(
        persona_prompt="保持冷静",
        persona_name="绫音",
        user_name="马达",
        current_speaker="马达",
        channel_type="group",
        space_label="测试群",
        active_participants=[],
        permanent_memories=[_memory("正在准备面试")],
        retrieved_memories=[
            _memory(
                "周末举行桌游活动",
                visibility=Visibility.FRIENDS_ONLY,
                space_id="测试群",
            ),
            _memory("服务维护时间", visibility=Visibility.PUBLIC),
        ],
        relationship=None,
    )

    assert "[当前发言者私有；群聊中勿主动披露] 正在准备面试" in system
    assert "[当前空间共享] 周末举行桌游活动" in system
    assert "[公开] 服务维护时间" in system


def test_direct_prompt_falls_back_to_legacy_display_name() -> None:
    system = render_main_dialogue_system(
        persona_prompt="保持冷静",
        persona_name="绫音",
        user_name="Alice",
        channel_type="direct",
        permanent_memories=[],
        retrieved_memories=[],
        relationship=None,
    )

    assert "当前发言者：Alice" in system
    assert "会话类型：私聊" in system
    assert "最近活跃参与者：Alice" in system
