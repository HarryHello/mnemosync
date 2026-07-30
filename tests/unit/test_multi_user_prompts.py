"""默认 Agent 提示词的多用户归属不变量测试."""

from __future__ import annotations

from src.core.agents.prompts.memory_analysis import build_memory_analysis_prompt
from src.core.agents.prompts.proxy_thinking import build_proxy_thinking_prompt
from src.core.agents.prompts.relationship_analysis import build_relationship_analysis_prompt


def test_memory_analysis_identifies_current_speaker_and_forbids_cross_user_attribution() -> None:
    prompt = build_memory_analysis_prompt(
        source_user="internal-group-uuid",
        current_speaker="马达 | astrbot 486394990",
        channel_type="group",
        conversation="user: Harry 喜欢咖啡",
        persona_name="绫音",
        persona_addressing="我",
        user_addressing="你",
        relation_context="尚未建立特定关系",
    )

    assert "当前发言者：马达 | astrbot 486394990" in prompt
    assert "会话类型：群聊" in prompt
    assert "第三人的陈述" in prompt
    assert "不能当作 Harry 已确认的事实" in prompt
    assert "__" not in prompt


def test_relationship_analysis_only_counts_signals_addressed_to_persona() -> None:
    prompt = build_relationship_analysis_prompt(
        current_relationship="stranger",
        current_speaker="马达",
        channel_type="group",
        conversation="user: Harry，以后叫我小哥",
        persona_name="绫音",
        persona_addressing="我",
        user_addressing="你",
        relation_context="尚未建立特定关系",
    )

    assert "只计算当前发言者直接面向人格的关系信号" in prompt
    assert "当前发言者对另一位群友说" in prompt
    assert "不更新其与人格的关系" in prompt
    assert "__" not in prompt


def test_proxy_thinking_requires_group_privacy_and_intervention_check() -> None:
    prompt = build_proxy_thinking_prompt(
        user_name="马达",
        relationship="friend",
        memories="正在准备面试",
        user_message="Harry 你怎么看？",
        channel_type="group",
    )

    assert "当前发言者：马达" in prompt
    assert "会话类型：群聊" in prompt
    assert "是否有必要介入" in prompt
    assert "不能公开表达" in prompt
    assert "__" not in prompt
