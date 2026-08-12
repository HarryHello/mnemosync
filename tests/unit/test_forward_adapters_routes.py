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
# 流式 chunk 转换: tool_calls 为 null 时的健壮性
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
    # 文本 delta 正常产出, 无工具调用事件
    assert any(e["type"] == "response.output_text.delta" for e in events)
    assert not any(e["type"] == "response.output_item.added" for e in events)


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
