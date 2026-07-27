"""OpenAI 工具调用协议透传与流式累积测试."""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.api.schemas.forward import ChatCompletionChoice, ChatMessage
from src.core.agents.factory import MainDialogueResult, run_main_dialogue
from src.infra.forwarder.forwarder import parse_sse_stream, parse_sse_stream_full
from src.infra.llm_service.models import ModelType


def _sse(data: str) -> bytes:
    return f"data: {data}\n\n".encode()


async def test_run_main_dialogue_preserves_tool_calls():
    forwarder = AsyncMock()
    forwarder.chat.return_value = {
        "choices": [
            {
                "index": 0,
                "message": {
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
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }
    tools = [
        {
            "type": "function",
            "function": {"name": "poke", "parameters": {"type": "object"}},
        }
    ]

    result = await run_main_dialogue(
        forwarder,
        [{"role": "user", "content": "戳一下他"}],
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=False,
    )

    assert isinstance(result, MainDialogueResult)
    assert result.finish_reason == "tool_calls"
    assert result.message["content"] is None
    assert result.message["tool_calls"][0]["function"]["name"] == "poke"
    forwarder.chat.assert_awaited_once_with(
        ModelType.MAIN,
        messages=[{"role": "user", "content": "戳一下他"}],
        temperature=0.7,
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=False,
    )


def test_chat_completion_choice_preserves_upstream_finish_reason_extension():
    choice = ChatCompletionChoice(
        index=0,
        message=ChatMessage(role="assistant", content="done"),
        finish_reason="eos",
    )

    assert choice.finish_reason == "eos"


def test_parse_sse_stream_full_accumulates_tool_arguments():
    chunks = [
        _sse(
            '{"choices":[{"index":0,"delta":{"role":"assistant",'
            '"tool_calls":[{"index":0,"id":"call_1","type":"function",'
            '"function":{"name":"poke","arguments":"{\\"user"}}]},'
            '"finish_reason":null}]}'
        ),
        _sse(
            '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"_id\\":\\"123\\"}"}}]},'
            '"finish_reason":null}]}'
        ),
        _sse(
            '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}'
        ),
        b"data: [DONE]\n\n",
    ]

    result = parse_sse_stream_full(chunks)

    assert result.text == ""
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "poke",
                "arguments": '{"user_id":"123"}',
            },
        }
    ]
    assert parse_sse_stream(chunks) == ""


def test_parse_sse_stream_full_handles_http_chunk_boundaries():
    payload = (
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
        b'"id":"call_1","type":"function","function":{"name":"poke",'
        b'"arguments":"{}"}}]},"finish_reason":"tool_calls"}]}\n\n'
        b'data: [DONE]\n\n'
    )
    split_at = payload.index(b'"arguments"') + 4

    result = parse_sse_stream_full([payload[:split_at], payload[split_at:]])

    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "poke", "arguments": "{}"},
        }
    ]


def test_parse_sse_stream_full_handles_text_and_multiple_tools():
    chunks = [
        _sse(
            '{"choices":[{"index":0,"delta":{"content":"先等等",'
            '"tool_calls":[{"index":0,"id":"call_a","type":"function",'
            '"function":{"name":"react","arguments":"{}"}},'
            '{"index":1,"id":"call_b","type":"function",'
            '"function":{"name":"poke","arguments":"{}"}}]},'
            '"finish_reason":null}]}'
        ),
        _sse(
            '{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}'
        ),
    ]

    result = parse_sse_stream_full(chunks)

    assert result.text == "先等等"
    assert result.finish_reason == "tool_calls"
    assert [tc["id"] for tc in result.tool_calls or []] == ["call_a", "call_b"]
    assert parse_sse_stream(chunks) == "先等等"
