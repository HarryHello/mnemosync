"""测试图节点行为 (尤其是 main_dialogue_node 的提前返回逻辑)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.core.graph.nodes import main_dialogue_node


@pytest.fixture(autouse=True)
def reset_settings():
    """确保每个测试前后都重置 settings 单例."""
    from src.core.config import _reset_settings

    _reset_settings()
    yield
    _reset_settings()


async def test_main_dialogue_node_returns_early_when_response_present():
    """当 state 已有 response 时, main_dialogue_node 直接返回, 不调用 LLM."""
    state = {
        "source_user": "default",
        "response": "预填充的回复文本",
    }

    with (
        patch("src.core.graph.nodes.run_main_dialogue", new=AsyncMock()) as mock_run_main,
        patch("src.core.graph.nodes._make_multi_forwarder") as mock_make_forwarder,
    ):
        result = await main_dialogue_node(state)

    assert result["response"] == "预填充的回复文本"
    mock_run_main.assert_not_called()
    mock_make_forwarder.assert_not_called()


async def test_main_dialogue_node_preserves_upstream_usage_when_present():
    """当 state 已有 upstream_usage 时, main_dialogue_node 也一并返回."""
    state = {
        "source_user": "default",
        "response": "预填充的回复",
        "upstream_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }

    with patch(
        "src.core.graph.nodes.run_main_dialogue",
        new=AsyncMock(),
    ):
        result = await main_dialogue_node(state)

    assert result["response"] == "预填充的回复"
    assert result["upstream_usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }


async def test_main_dialogue_node_generates_when_response_missing():
    """非流式 state 没有预填充 response 时, 仍调用主对话 LLM 生成回复."""
    settings = SimpleNamespace(
        storage=SimpleNamespace(memory_db_abs="memory.db", chroma_dir_abs="chroma"),
        memory=SimpleNamespace(permanent_load_top=5, retrieval_top_k=5),
        persona=SimpleNamespace(prompt="persona", name="assistant"),
    )
    forwarder = MagicMock()
    forwarder.close = AsyncMock()
    memory_store = MagicMock()
    memory_store.init_db = AsyncMock()
    memory_store.list_permanent = AsyncMock(return_value=[])
    memory_store.get_relationship = AsyncMock(return_value=None)
    run_main = AsyncMock(
        return_value=(
            "新生成的回复",
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
    )

    with (
        patch("src.core.graph.nodes.get_settings", return_value=settings),
        patch("src.core.graph.nodes._make_multi_forwarder", return_value=forwarder),
        patch("src.core.graph.nodes.SqliteMemoryStore", return_value=memory_store),
        patch("src.core.graph.nodes.VectorStore"),
        patch("src.core.graph.nodes.build_main_dialogue_messages", return_value=[]) as build,
        patch("src.core.graph.nodes.run_main_dialogue", new=run_main),
    ):
        result = await main_dialogue_node(
            {
                "source_user": "default",
                "messages": [{"role": "user", "content": "你好"}],
                "extracted_new": [],
            }
        )

    assert result == {
        "response": "新生成的回复",
        "upstream_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "emotion_analysis": {"emotion": "neutral", "intensity": 0.0, "category": "other", "keywords": [], "summary": ""},
    }
    build.assert_called_once()
    run_main.assert_awaited_once_with(forwarder, [])
    forwarder.close.assert_awaited_once()
