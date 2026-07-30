"""测试情绪分析工具的调试 Agent 标记."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from src.infra.debug_context import get_agent_name, use_agent
from src.tools.emotion_analyzer import analyze_emotion


async def test_analyze_emotion_uses_own_agent_label_and_restores_parent():
    """情绪分析调用使用独立标签, 完成后恢复外层 Agent 标签."""
    labels_during_call: list[str | None] = []

    async def chat(*args, **kwargs):
        labels_during_call.append(get_agent_name())
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "emotion": "happy",
                                "intensity": 0.8,
                                "category": "emotional_expression",
                                "keywords": ["开心"],
                                "summary": "表达开心",
                            }
                        )
                    }
                }
            ]
        }

    forwarder = MagicMock()
    forwarder.chat = AsyncMock(side_effect=chat)

    with use_agent("memory_analysis"):
        result = await analyze_emotion(forwarder, "今天很开心")
        assert get_agent_name() == "memory_analysis"

    assert labels_during_call == ["emotion_analysis"]
    assert result.emotion == "happy"
    assert get_agent_name() is None
