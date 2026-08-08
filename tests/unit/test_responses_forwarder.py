"""ResponsesForwarder 格式转换函数测试.

覆盖 Chat Completions ↔ Responses API 消息/响应转换.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.infra.forwarder.responses import (
    _convert_chat_to_responses,
    _convert_responses_to_chat,
)

# ---------------------------------------------------------------------------
# _convert_chat_to_responses
# ---------------------------------------------------------------------------


def test_system_becomes_instructions() -> None:
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ]
    result = _convert_chat_to_responses(messages)
    assert result["instructions"] == "你是助手"
    # system 不在 input 中
    assert all(item.get("role") != "system" for item in result["input"])
    assert result["input"] == [{"role": "user", "content": "你好"}]


def test_user_text_message() -> None:
    messages = [{"role": "user", "content": "hello"}]
    result = _convert_chat_to_responses(messages)
    assert result["input"] == [{"role": "user", "content": "hello"}]
    assert "instructions" not in result


def test_user_multimodal_content() -> None:
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "https://x.com/a.png"}},
    ]}]
    result = _convert_chat_to_responses(messages)
    content = result["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "看图"}
    assert content[1] == {"type": "input_image", "image_url": "https://x.com/a.png"}


def test_tool_message_becomes_function_call_output() -> None:
    messages = [
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]
    result = _convert_chat_to_responses(messages)
    item = result["input"][0]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_1"
    assert item["output"] == "result"


def test_tools_converted() -> None:
    messages = [{"role": "user", "content": "hi"}]
    tools = [{
        "type": "function",
        "function": {
            "name": "fn",
            "description": "a function",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    result = _convert_chat_to_responses(messages, tools=tools)
    assert result["tools"][0]["type"] == "function"
    assert result["tools"][0]["name"] == "fn"


# ---------------------------------------------------------------------------
# _convert_responses_to_chat
# ---------------------------------------------------------------------------


def _make_responses_output(text=None, tool_call=None):
    output = []
    if text is not None:
        output.append(SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text", text=text)],
        ))
    if tool_call is not None:
        output.append(SimpleNamespace(
            type="function_call",
            call_id=tool_call.get("call_id"),
            name=tool_call.get("name"),
            arguments=tool_call.get("arguments"),
        ))
    return output


def test_convert_text_response() -> None:
    resp = SimpleNamespace(
        id="resp_1",
        output=_make_responses_output(text="你好"),
        usage=SimpleNamespace(input_tokens=5, output_tokens=3, total_tokens=8),
    )
    result = _convert_responses_to_chat(resp, "gpt-x")
    assert result["object"] == "chat.completion"
    assert result["choices"][0]["message"]["content"] == "你好"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 8


def test_convert_tool_call_response() -> None:
    resp = SimpleNamespace(
        id="resp_2",
        output=_make_responses_output(tool_call={
            "call_id": "fc_1", "name": "get_weather", "arguments": '{"city":"北京"}',
        }),
        usage=None,
    )
    result = _convert_responses_to_chat(resp, "gpt-x")
    msg = result["choices"][0]["message"]
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
    assert msg["tool_calls"][0]["id"] == "fc_1"
