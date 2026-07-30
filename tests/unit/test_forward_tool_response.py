"""非流式工具调用响应封装测试."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.api.routes.forward import _handle_non_stream
from src.api.schemas.forward import ChatCompletionRequest, ChatMessage
from src.api.state import AppState


async def test_non_stream_response_preserves_tool_calls_and_finish_reason():
    request = ChatCompletionRequest(
        model="mnemosync-any",
        messages=[ChatMessage(role="user", content="戳一下他")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "poke",
                    "parameters": {"type": "object"},
                },
            }
        ],
        stream=False,
    )
    conversation_store = SimpleNamespace(
        append=AsyncMock(),
    )
    built = SimpleNamespace(
        conversation_history=[],
        active_participants=[],
        kept=0,
        total_candidates=0,
        budget=1000,
    )
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "response": "",
                "response_message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "poke",
                                "arguments": '{"user_id":"123"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
                "upstream_usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            }
        )
    )
    http_request = SimpleNamespace(
        app=SimpleNamespace(
            state=AppState(
                conversation_store=conversation_store,
                resolver=SimpleNamespace(),
            )
        )
    )
    initial_state = {
        "messages": [{"role": "user", "content": "戳一下他"}],
        "source_user": "user-1",
        "main_model": "test-model",
    }
    settings = SimpleNamespace(
        storage=SimpleNamespace(short_term_days=7),
        persona=SimpleNamespace(prompt="persona"),
    )

    with (
        patch("src.api.routes.forward.nonstream.get_settings", return_value=settings),
        patch("src.api.routes.forward.nonstream._resolve_main_candidate", new=AsyncMock(return_value=None)),
        patch("src.api.routes.forward.nonstream.build_short_term_history", new=AsyncMock(return_value=built)),
        patch("src.api.routes.forward.nonstream._get_compiled_graph", return_value=graph),
        patch("src.api.routes.forward.nonstream._record_idempotency", new=AsyncMock()),
    ):
        response = await _handle_non_stream(http_request, initial_state, request)

    body = json.loads(response.body)
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "poke"
    assert choice["message"]["tool_calls"][0]["function"]["arguments"] == '{"user_id":"123"}'
    assert body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    graph.ainvoke.assert_awaited_once()
