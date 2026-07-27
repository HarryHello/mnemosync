"""工具事务尾部接入非流式与流式主请求的测试."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.api.routes.forward import _handle_non_stream, _handle_stream
from src.api.schemas.forward import ChatCompletionRequest, ChatMessage
from src.api.tool_transactions import extract_tool_transaction_tail

TOOLS = [
    {
        "type": "function",
        "function": {"name": "poke", "parameters": {"type": "object"}},
    }
]
CLIENT_MESSAGES = [
    {"role": "user", "content": "戳一下他"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "poke", "arguments": '{"user_id":"123"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "success"},
]


def _request(*, stream: bool) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="mnemosync-any",
        messages=[ChatMessage.model_validate(message) for message in CLIENT_MESSAGES],
        tools=TOOLS,
        stream=stream,
    )


def _built() -> SimpleNamespace:
    return SimpleNamespace(
        conversation_history=[{"role": "assistant", "content": "更早的服务器历史"}],
        active_participants=[],
        kept=1,
        total_candidates=1,
        budget=1000,
        used=10,
        dropped_by_budget=0,
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        storage=SimpleNamespace(short_term_days=7, memory_db_abs="memory.db", chroma_dir_abs="chroma"),
        memory=SimpleNamespace(permanent_load_top=5, retrieval_top_k=5),
        persona=SimpleNamespace(prompt="persona", name="assistant"),
    )


async def test_non_stream_tool_result_appends_validated_transaction_without_new_user():
    request = _request(stream=False)
    transaction = extract_tool_transaction_tail(CLIENT_MESSAGES, TOOLS)
    assert transaction is not None
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "response": "戳完了",
                "response_message": {"role": "assistant", "content": "戳完了"},
                "finish_reason": "stop",
            }
        )
    )
    conversation_store = SimpleNamespace(append=AsyncMock())
    http_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                conversation_store=conversation_store,
                resolver=SimpleNamespace(),
            )
        )
    )
    initial_state = {
        "messages": CLIENT_MESSAGES,
        "tool_transaction": transaction,
        "source_user": "user-1",
        "main_model": "test-model",
    }

    with (
        patch("src.api.routes.forward.get_settings", return_value=_settings()),
        patch("src.api.routes.forward._resolve_main_candidate", new=AsyncMock(return_value=None)),
        patch("src.api.routes.forward.build_short_term_history", new=AsyncMock(return_value=_built())),
        patch("src.api.routes.forward._get_compiled_graph", return_value=graph),
        patch("src.api.routes.forward._record_idempotency", new=AsyncMock()),
    ):
        response = await _handle_non_stream(http_request, initial_state, request)

    assert json.loads(response.body)["choices"][0]["message"]["content"] == "戳完了"
    invoked = graph.ainvoke.await_args.args[0]
    assert invoked["extracted_new"] == [
        {"role": "user", "content": "戳一下他"}
    ]
    assert [message["role"] for message in invoked["messages"]] == [
        "assistant", "user", "assistant", "tool",
    ]
    assert invoked["messages"][-1]["tool_call_id"] == "call_1"
    # 工具续轮不能把根 user 再次作为当前事件写入；只落最终 assistant。
    conversation_store.append.assert_awaited_once()
    assert conversation_store.append.await_args.kwargs["role"] == "assistant"


async def test_stream_tool_result_appends_validated_transaction_to_upstream_messages():
    request = _request(stream=True)
    transaction = extract_tool_transaction_tail(CLIENT_MESSAGES, TOOLS)
    assert transaction is not None
    conversation_store = SimpleNamespace(append=AsyncMock())
    multi_forwarder = SimpleNamespace(close=AsyncMock())

    async def chat_stream(*args, **kwargs):
        yield (
            b'data: {"choices":[{"index":0,"delta":{"content":"done"},'
            b'"finish_reason":"stop"}]}\n\n'
        )
        yield b"data: [DONE]\n\n"

    multi_forwarder.chat_stream = chat_stream
    http_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                conversation_store=conversation_store,
                multi_forwarder=multi_forwarder,
                resolver=SimpleNamespace(),
            )
        )
    )
    initial_state = {
        "messages": CLIENT_MESSAGES,
        "tool_transaction": transaction,
        "source_user": "user-1",
        "current_speaker": "Harry",
        "persona": "persona",
        "persona_name": "assistant",
        "persona_id": "default",
        "main_model": "test-model",
        "channel_type": "group",
    }
    memory_store = SimpleNamespace(
        init_db=AsyncMock(),
        get_relationship=AsyncMock(return_value=None),
        list_permanent=AsyncMock(return_value=[]),
    )

    with (
        patch("src.api.routes.forward.get_settings", return_value=_settings()),
        patch("src.api.routes.forward._resolve_main_candidate", new=AsyncMock(return_value=None)),
        patch("src.api.routes.forward.SqliteMemoryStore", return_value=memory_store),
        patch("src.api.routes.forward.VectorStore"),
        patch("src.api.routes.forward.MemoryRetriever") as retriever_cls,
        patch("src.api.routes.forward.build_short_term_history", new=AsyncMock(return_value=_built())),
        patch("src.api.routes.forward.build_main_dialogue_messages", return_value=[]) as build_messages,
        patch("src.api.routes.forward._record_idempotency", new=AsyncMock()),
        patch("src.api.routes.forward._run_memory_graph", new=AsyncMock()),
    ):
        retriever_cls.return_value.search = AsyncMock(return_value=[])
        response = await _handle_stream(http_request, initial_state, request, False)
        chunks = [chunk async for chunk in response.body_iterator]

    assert any(b"done" in chunk for chunk in chunks)
    history = build_messages.call_args.kwargs["conversation_history"]
    assert [message["role"] for message in history] == [
        "assistant", "user", "assistant", "tool",
    ]
    assert initial_state["extracted_new"] == [
        {"role": "user", "content": "戳一下他"}
    ]
    # 工具续轮不能把根 user 再次作为当前事件写入；只落最终 assistant。
    conversation_store.append.assert_awaited_once()
    assert conversation_store.append.await_args.kwargs["role"] == "assistant"
