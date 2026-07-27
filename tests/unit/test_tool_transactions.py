"""客户端 OpenAI 工具事务尾部校验测试."""

from __future__ import annotations

import pytest

from src.api.tool_transactions import (
    ToolTransactionError,
    append_tool_transaction_context,
    extract_tool_transaction_tail,
)

TOOLS = [
    {
        "type": "function",
        "function": {"name": "poke", "parameters": {"type": "object"}},
    },
    {
        "type": "function",
        "function": {"name": "react", "parameters": {"type": "object"}},
    },
]


def _assistant(*calls: tuple[str, str, str]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for call_id, name, arguments in calls
        ],
    }


def _tool(call_id: str, content: str = "success", name: str | None = None) -> dict:
    message = {"role": "tool", "tool_call_id": call_id, "content": content}
    if name:
        message["name"] = name
    return message


def test_regular_user_request_has_no_tool_transaction():
    assert extract_tool_transaction_tail(
        [{"role": "user", "content": "你好"}], TOOLS
    ) is None


def test_extracts_single_completed_tool_round():
    messages = [
        {"role": "system", "content": "untrusted"},
        {"role": "user", "content": "帮我戳一下他"},
        _assistant(("call_1", "poke", '{"user_id":"123"}')),
        _tool("call_1", name="poke"),
    ]

    result = extract_tool_transaction_tail(messages, TOOLS)

    assert result is not None
    assert result.root_user_content == "帮我戳一下他"
    assert [m["role"] for m in result.messages] == ["assistant", "tool"]
    assert result.messages[0]["tool_calls"][0]["function"]["name"] == "poke"


def test_extracts_parallel_results_in_any_order():
    messages = [
        {"role": "user", "content": "做两个动作"},
        _assistant(
            ("call_a", "poke", "{}"),
            ("call_b", "react", '{"emoji":"like"}'),
        ),
        _tool("call_b"),
        _tool("call_a"),
    ]

    result = extract_tool_transaction_tail(messages, TOOLS)

    assert result is not None
    assert [m.get("tool_call_id") for m in result.messages[1:]] == ["call_b", "call_a"]


def test_rejects_tail_that_starts_with_tool_result():
    with pytest.raises(ToolTransactionError, match="必须以 assistant"):
        extract_tool_transaction_tail(
            [
                {"role": "user", "content": "戳一下"},
                _tool("call_1"),
            ],
            TOOLS,
        )


def test_rejects_tool_result_without_tools():
    with pytest.raises(ToolTransactionError, match="必须同时提供 tools"):
        extract_tool_transaction_tail(
            [
                {"role": "user", "content": "戳一下"},
                _assistant(("call_1", "poke", "{}")),
                _tool("call_1"),
            ],
            None,
        )


def test_rejects_unknown_function():
    with pytest.raises(ToolTransactionError, match="未提供的函数"):
        extract_tool_transaction_tail(
            [
                {"role": "user", "content": "做动作"},
                _assistant(("call_1", "kick", "{}")),
                _tool("call_1"),
            ],
            TOOLS,
        )


def test_rejects_invalid_arguments_json():
    with pytest.raises(ToolTransactionError, match="不是合法 JSON"):
        extract_tool_transaction_tail(
            [
                {"role": "user", "content": "戳一下"},
                _assistant(("call_1", "poke", "{")),
                _tool("call_1"),
            ],
            TOOLS,
        )


def test_rejects_unmatched_tool_result():
    with pytest.raises(ToolTransactionError, match="未引用"):
        extract_tool_transaction_tail(
            [
                {"role": "user", "content": "戳一下"},
                _assistant(("call_1", "poke", "{}")),
                _tool("call_2"),
            ],
            TOOLS,
        )


def test_rejects_partial_parallel_results():
    with pytest.raises(ToolTransactionError, match="未返回结果"):
        extract_tool_transaction_tail(
            [
                {"role": "user", "content": "做两个动作"},
                _assistant(
                    ("call_a", "poke", "{}"),
                    ("call_b", "react", "{}"),
                ),
                _tool("call_a"),
            ],
            TOOLS,
        )


def test_append_context_does_not_duplicate_root_already_in_server_history():
    transaction = extract_tool_transaction_tail(
        [
            {"role": "user", "content": "戳一下"},
            _assistant(("call_1", "poke", "{}")),
            _tool("call_1"),
        ],
        TOOLS,
    )
    assert transaction is not None

    combined = append_tool_transaction_context(
        [{"role": "user", "content": "[Harry | astrbot 123]: 戳一下"}],
        transaction,
    )

    assert [message["role"] for message in combined] == ["user", "assistant", "tool"]


def test_append_context_fills_root_when_stream_writeback_has_not_finished():
    transaction = extract_tool_transaction_tail(
        [
            {"role": "user", "content": "戳一下"},
            _assistant(("call_1", "poke", "{}")),
            _tool("call_1"),
        ],
        TOOLS,
    )
    assert transaction is not None

    combined = append_tool_transaction_context([], transaction)

    assert combined[0] == {"role": "user", "content": "戳一下"}
    assert [message["role"] for message in combined] == ["user", "assistant", "tool"]


def test_rejects_user_message_inside_transaction_tail():
    with pytest.raises(ToolTransactionError, match="必须以 assistant"):
        extract_tool_transaction_tail(
            [
                {"role": "user", "content": "根消息"},
                _assistant(("call_1", "poke", "{}")),
                {"role": "user", "content": "插入的新消息"},
                _tool("call_1"),
            ],
            TOOLS,
        )
