"""AnthropicForwarder 格式转换函数测试.

覆盖 OpenAI ↔ Anthropic 消息/工具/响应转换, 以及 MultiForwarder 路由.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.infra.forwarder.anthropic import (
    _convert_anthropic_response_to_openai,
    _convert_messages_to_anthropic,
    _convert_tools_to_anthropic,
    _map_stop_reason,
    _parse_data_url,
)

# ---------------------------------------------------------------------------
# _convert_messages_to_anthropic
# ---------------------------------------------------------------------------


def test_system_extracted_to_top_level() -> None:
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ]
    system, msgs = _convert_messages_to_anthropic(messages)
    assert system == "你是助手"
    assert msgs == [{"role": "user", "content": "你好"}]


def test_plain_text_messages_passthrough() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    system, msgs = _convert_messages_to_anthropic(messages)
    assert system is None
    assert msgs == messages


def test_image_url_part_converted() -> None:
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "https://x.com/a.png"}},
        ]},
    ]
    _, msgs = _convert_messages_to_anthropic(messages)
    content = msgs[0]["content"]
    assert content[0] == {"type": "text", "text": "看图"}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["type"] == "url"
    assert content[1]["source"]["url"] == "https://x.com/a.png"


def test_base64_image_converted() -> None:
    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ABC123"}},
        ]},
    ]
    _, msgs = _convert_messages_to_anthropic(messages)
    img = msgs[0]["content"][0]
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    assert img["source"]["data"] == "ABC123"


def test_tool_message_converted_to_tool_result() -> None:
    messages = [
        {"role": "tool", "tool_call_id": "call_1", "content": "result data"},
    ]
    _, msgs = _convert_messages_to_anthropic(messages)
    assert msgs[0]["role"] == "user"
    block = msgs[0]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_1"
    assert block["content"] == "result data"


# ---------------------------------------------------------------------------
# _parse_data_url
# ---------------------------------------------------------------------------


def test_parse_data_url() -> None:
    media_type, data = _parse_data_url("data:image/jpeg;base64,XYZ")
    assert media_type == "image/jpeg"
    assert data == "XYZ"


# ---------------------------------------------------------------------------
# _convert_tools_to_anthropic
# ---------------------------------------------------------------------------


def test_convert_tools() -> None:
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取天气",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }]
    result = _convert_tools_to_anthropic(tools)
    assert result is not None
    assert result[0]["name"] == "get_weather"
    assert result[0]["description"] == "获取天气"
    assert result[0]["input_schema"]["properties"]["city"]["type"] == "string"


def test_convert_tools_none() -> None:
    assert _convert_tools_to_anthropic(None) is None
    assert _convert_tools_to_anthropic([]) is None


# ---------------------------------------------------------------------------
# _map_stop_reason
# ---------------------------------------------------------------------------


def test_map_stop_reason() -> None:
    assert _map_stop_reason("end_turn") == "stop"
    assert _map_stop_reason("max_tokens") == "length"
    assert _map_stop_reason("tool_use") == "tool_calls"
    assert _map_stop_reason(None) == "stop"
    assert _map_stop_reason("unknown") == "stop"


# ---------------------------------------------------------------------------
# _convert_anthropic_response_to_openai
# ---------------------------------------------------------------------------


def _make_anthropic_response(content, stop_reason="end_turn", in_tok=10, out_tok=5):
    return SimpleNamespace(
        id="msg_123",
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


def test_convert_text_response() -> None:
    resp = _make_anthropic_response([SimpleNamespace(type="text", text="你好世界")])
    result = _convert_anthropic_response_to_openai(resp, "claude-3")
    assert result["object"] == "chat.completion"
    assert result["model"] == "claude-3"
    assert result["choices"][0]["message"]["content"] == "你好世界"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 15


def test_convert_tool_use_response() -> None:
    resp = _make_anthropic_response(
        [SimpleNamespace(type="tool_use", id="tu_1", name="get_weather", input={"city": "北京"})],
        stop_reason="tool_use",
    )
    result = _convert_anthropic_response_to_openai(resp, "claude-3")
    msg = result["choices"][0]["message"]
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
    assert '"city"' in msg["tool_calls"][0]["function"]["arguments"]
