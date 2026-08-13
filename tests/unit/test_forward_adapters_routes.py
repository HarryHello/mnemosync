"""下游多格式 adapter 路由回归测试.

验证 POST /v1/messages (Anthropic) 与 POST /v1/responses (Responses API)
确实注册在 /v1/ 下 (而非 /v1/v1/ 的 prefix 嵌套 bug), 且内部格式转回正确.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from src.cli.cli import _build_api_app


@pytest.fixture
def client() -> TestClient:
    app = _build_api_app(SimpleNamespace(debug=False))
    with TestClient(app) as c:
        yield c


def test_responses_endpoint_is_post_not_405(client: TestClient) -> None:
    """POST /v1/responses 命中 adapter (而非 SPA 兜底的 405)."""
    r = client.post("/v1/responses", json={
        "model": "mnemosync-any",
        "input": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code != 405
    assert r.status_code == 200
    body = r.json()
    assert body.get("object") == "response"  # Responses API 格式


def test_messages_endpoint_is_post_not_405(client: TestClient) -> None:
    """POST /v1/messages 命中 adapter (而非 SPA 兜底的 405)."""
    r = client.post("/v1/messages", json={
        "model": "mnemosync-any",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100,
    })
    assert r.status_code != 405
    assert r.status_code == 200
    body = r.json()
    assert body.get("type") == "message"  # Anthropic Messages 格式


def test_no_prefix_nesting(client: TestClient) -> None:
    """/v1/v1/responses 不应存在 (prefix 拼接 bug 的残留路径)."""
    r = client.post("/v1/v1/responses", json={
        "model": "x", "input": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code in (404, 405)


# ---------------------------------------------------------------------------
# 流式 chunk 转换: 协议顺序 + tool_calls 为 null 的健壮性
# ---------------------------------------------------------------------------


def test_responses_chunk_with_null_tool_calls() -> None:
    """SSE chunk 中 delta.tool_calls 显式为 null 不应崩溃."""
    from src.api.routes.forward.responses_adapter import _convert_chat_chunk_to_responses

    chunk = {
        "choices": [{
            "index": 0,
            "delta": {"content": "你好", "tool_calls": None},
            "finish_reason": None,
        }],
    }
    events = _convert_chat_chunk_to_responses(chunk, "resp_x")
    # 首次出现文本: 先 output_item.added (message) → content_part.added → output_text.delta
    types = [e["type"] for e in events]
    assert types[0] == "response.output_item.added"
    assert types[1] == "response.content_part.added"
    assert "response.output_text.delta" in types
    assert not any(e["type"] == "response.completed" for e in events)


def test_responses_stream_full_sequence() -> None:
    """完整流: 文本 chunk → 结束 chunk, 事件序列符合 Responses 协议 (含 AI SDK zod 必填字段)."""
    from src.api.routes.forward.responses_adapter import (
        _convert_chat_chunk_to_responses,
        _ResponsesStreamState,
    )

    state = _ResponsesStreamState()
    all_events: list[dict] = []
    all_events += _convert_chat_chunk_to_responses(
        {"choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}]},
        "resp_x", state,
    )
    all_events += _convert_chat_chunk_to_responses(
        {"choices": [{"index": 0, "delta": {"content": "!"}, "finish_reason": None}]},
        "resp_x", state,
    )
    all_events += _convert_chat_chunk_to_responses(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        "resp_x", state,
    )
    types = [e["type"] for e in all_events]

    assert types[0] == "response.output_item.added"
    assert types[1] == "response.content_part.added"
    assert types.count("response.output_text.delta") == 2
    assert "response.output_text.done" in types
    assert "response.output_item.done" in types
    assert types[-1] == "response.completed"

    # AI SDK zod 必填: output_item.done 带完整 message item (id + content)
    done_item = next(e for e in all_events if e["type"] == "response.output_item.done")
    assert done_item["item"]["type"] == "message"
    assert done_item["item"]["id"]
    assert done_item["item"]["content"][0]["text"] == "你好!"

    # AI SDK zod 必填: completed 的 response 带 usage (input_tokens/output_tokens)
    completed = next(e for e in all_events if e["type"] == "response.completed")
    assert "input_tokens" in completed["response"]["usage"]
    assert "output_tokens" in completed["response"]["usage"]


def test_responses_stream_tool_call_done_item() -> None:
    """工具调用流: output_item.done 带完整 function_call item (含累积 arguments)."""
    from src.api.routes.forward.responses_adapter import (
        _convert_chat_chunk_to_responses,
        _ResponsesStreamState,
    )

    state = _ResponsesStreamState()
    all_events: list[dict] = []
    all_events += _convert_chat_chunk_to_responses(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_", "arguments": ""}},
        ]}, "finish_reason": None}]},
        "resp_x", state,
    )
    all_events += _convert_chat_chunk_to_responses(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"name": "weather", "arguments": "{}"}},
        ]}, "finish_reason": None}]},
        "resp_x", state,
    )
    all_events += _convert_chat_chunk_to_responses(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        "resp_x", state,
    )

    done_items = [e for e in all_events if e["type"] == "response.output_item.done"]
    assert len(done_items) == 1
    item = done_items[0]["item"]
    assert item["type"] == "function_call"
    assert item["name"] == "get_weather"
    assert item["arguments"] == "{}"
    assert item["status"] == "completed"


def test_anthropic_chunk_with_null_tool_calls() -> None:
    """SSE chunk 中 delta.tool_calls 显式为 null 不应崩溃."""
    from src.api.routes.forward.anthropic_adapter import _convert_openai_chunk_to_anthropic

    chunk = {
        "choices": [{
            "index": 0,
            "delta": {"content": "你好", "tool_calls": None},
            "finish_reason": None,
        }],
    }
    events = _convert_openai_chunk_to_anthropic(chunk)
    assert any(e["type"] == "content_block_delta" for e in events)


def test_anthropic_tool_call_fragmented_name() -> None:
    """工具名跨帧分片: 只 start 一次, name 累积, 不重复 content_block_start."""
    from src.api.routes.forward.anthropic_adapter import (
        _AnthropicStreamState,
        _convert_openai_chunk_to_anthropic,
    )

    state = _AnthropicStreamState()
    events: list[dict] = []
    events += _convert_openai_chunk_to_anthropic(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_", "arguments": ""}},
        ]}, "finish_reason": None}]},
        state,
    )
    events += _convert_openai_chunk_to_anthropic(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"name": "weather", "arguments": "{}"}},
        ]}, "finish_reason": None}]},
        state,
    )
    events += _convert_openai_chunk_to_anthropic(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        state,
    )

    starts = [e for e in events if e["type"] == "content_block_start"]
    assert len(starts) == 1  # 只 start 一次
    assert starts[0]["content_block"]["type"] == "tool_use"
    assert starts[0]["content_block"]["name"] == "get_weather"
    assert starts[0]["content_block"]["id"] == "call_1"
    # 结束时 message_delta 带 tool_use stop_reason
    deltas = [e for e in events if e["type"] == "message_delta"]
    assert deltas and deltas[0]["delta"]["stop_reason"] == "tool_use"


def test_anthropic_text_then_tool_switches_blocks() -> None:
    """文本后出现工具调用: 先 stop 文本块, 再 start tool_use 块."""
    from src.api.routes.forward.anthropic_adapter import (
        _AnthropicStreamState,
        _convert_openai_chunk_to_anthropic,
    )

    state = _AnthropicStreamState()
    events: list[dict] = []
    events += _convert_openai_chunk_to_anthropic(
        {"choices": [{"index": 0, "delta": {"content": "我先查一下"}, "finish_reason": None}]},
        state,
    )
    events += _convert_openai_chunk_to_anthropic(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": "{}"}},
        ]}, "finish_reason": None}]},
        state,
    )
    events += _convert_openai_chunk_to_anthropic(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        state,
    )

    types = [e["type"] for e in events]
    # 文本块: start → delta → (切换时) stop
    assert types[0] == "content_block_start"
    assert types[1] == "content_block_delta"
    stop_idx = types.index("content_block_stop")
    # 工具块: start → delta
    assert "content_block_start" in types[stop_idx + 1:]
    # 收尾: 工具块 stop + message_delta (tool_use)
    assert types[-1] == "message_delta"
    assert events[-1]["delta"]["stop_reason"] == "tool_use"


def test_anthropic_request_tool_use_converts_to_tool_calls() -> None:
    """Anthropic 请求的 assistant tool_use block → OpenAI tool_calls (不再丢弃)."""
    from src.api.routes.forward.anthropic_adapter import (
        AnthropicContentBlock,
        AnthropicMessage,
        _convert_anthropic_to_openai,
    )

    body = SimpleNamespace(
        model="mnemosync-any",
        messages=[
            AnthropicMessage(role="user", content="天气如何"),
            AnthropicMessage(role="assistant", content=[
                AnthropicContentBlock(
                    type="text", text="我来查",
                ),
                AnthropicContentBlock(
                    type="tool_use", id="toolu_1", name="get_weather",
                    input={"city": "北京"},
                ),
            ]),
            AnthropicMessage(role="user", content=[
                AnthropicContentBlock(
                    type="tool_result", tool_use_id="toolu_1", content="晴",
                ),
            ]),
        ],
        system=None,
        max_tokens=4096,
        temperature=1.0,
        stream=False,
        tools=None,
        tool_choice=None,
        metadata=None,
    )
    result = _convert_anthropic_to_openai(body)
    msgs = result["messages"]
    # assistant 消息带 tool_calls
    assistant = next(m for m in msgs if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant["tool_calls"][0]["id"] == "toolu_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "get_weather"
    assert '"city"' in assistant["tool_calls"][0]["function"]["arguments"]
    # tool_result → tool 消息
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "toolu_1"
    assert tool_msg["content"] == "晴"


def test_count_tokens_endpoint(client: TestClient) -> None:
    """Cherry Studio 调 POST /v1/messages/count_tokens 应返回 input_tokens 而非 405."""
    r = client.post("/v1/messages/count_tokens", json={
        "model": "mnemosync-any",
        "messages": [{"role": "user", "content": "你好世界"}],
    })
    assert r.status_code != 405
    assert r.status_code == 200
    body = r.json()
    assert "input_tokens" in body
    assert body["input_tokens"] > 0


def test_anthropic_nonstream_reasoning_to_thinking() -> None:
    """下游非流式: OpenAI reasoning_content → Anthropic thinking block."""
    from src.api.routes.forward.anthropic_adapter import _convert_openai_to_anthropic_response

    resp = {"choices": [{"message": {
        "role": "assistant",
        "content": "你好",
        "reasoning_content": "内心思考",
    }, "finish_reason": "stop"}]}
    result = _convert_openai_to_anthropic_response(resp)
    blocks = result["content"]
    assert any(b["type"] == "thinking" and b["thinking"] == "内心思考" for b in blocks)
    assert any(b["type"] == "text" and b["text"] == "你好" for b in blocks)


def test_responses_nonstream_reasoning_item() -> None:
    """下游非流式: OpenAI reasoning_content → Responses reasoning item."""
    from src.api.routes.forward.responses_adapter import _convert_chat_to_responses

    resp = {"choices": [{"message": {
        "role": "assistant",
        "content": "你好",
        "reasoning_content": "内心思考",
    }}]}
    result = _convert_chat_to_responses(resp)
    assert result["object"] == "response"
    assert "created_at" in result
    reasoning = next(o for o in result["output"] if o["type"] == "reasoning")
    assert reasoning["summary"][0]["text"] == "内心思考"
    msg = next(o for o in result["output"] if o["type"] == "message")
    assert msg["content"][0]["text"] == "你好"


def test_429_classify_rate_vs_quota() -> None:
    """429 分类: 限流 vs 配额 vs 未知."""
    from src.infra.forwarder.forwarder import UpstreamError, classify_429

    assert classify_429('{"error": {"type": "rate_limit_error", "message": "rate limit"}}') == "rate"
    assert classify_429('{"error": {"code": "too_many_requests"}}') == "rate"
    assert classify_429('{"error": {"code": "insufficient_quota", "message": "quota"}}') == "quota"
    assert classify_429('{"error": {"message": "insufficient balance to cover the request"}}') == "quota"
    assert classify_429("plain text no keywords") == "unknown"

    # fallback 行为: 配额类 429 fallback; 限流不 fallback
    from src.infra.forwarder.multi import _should_fallback

    assert _should_fallback(UpstreamError(429, '{"error": {"code": "insufficient_quota"}}')) is True
    assert _should_fallback(UpstreamError(429, '{"error": {"code": "rate_limit_error"}}')) is False
    assert _should_fallback(UpstreamError(429, "plain 429")) is False
    assert _should_fallback(UpstreamError(400, "bad")) is False
