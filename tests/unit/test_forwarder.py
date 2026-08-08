"""Forwarder 单元测试.

覆盖:
- parse_sse_stream / parse_sse_stream_full: SSE 字节块解析
- UpstreamError / UpstreamTimeout: 异常类
- ForwarderConfig: 数据类构建
- Forwarder._headers / chat / embed / rerank: HTTP 转发逻辑 (mock openai SDK)
- _should_fallback: MultiForwarder 的异常分类
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError
from src.infra.forwarder.forwarder import (
    Forwarder,
    ForwarderConfig,
    StreamResult,
    UpstreamError,
    UpstreamTimeout,
    parse_sse_stream,
    parse_sse_stream_full,
)
from src.infra.forwarder.multi import _should_fallback


def _make_mock_openai_client(
    *,
    chat_result: dict | None = None,
    embed_result: dict | None = None,
    chat_side_effect: Exception | None = None,
    embed_side_effect: Exception | None = None,
):
    """Create a mock AsyncOpenAI client."""
    mock_client = AsyncMock()

    # Chat completions
    if chat_side_effect:
        mock_client.chat.completions.create = AsyncMock(side_effect=chat_side_effect)
    else:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = chat_result or {
            "choices": [{"message": {"content": "hi"}}]
        }
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    # Embeddings
    if embed_side_effect:
        mock_client.embeddings.create = AsyncMock(side_effect=embed_side_effect)
    else:
        mock_embed_resp = MagicMock()
        if embed_result:
            mock_embed_resp.data = [
                MagicMock(embedding=item["embedding"], index=item["index"])
                for item in embed_result.get("data", [])
            ]
        else:
            mock_embed_resp.data = []
        mock_client.embeddings.create = AsyncMock(return_value=mock_embed_resp)

    # Models
    mock_models_resp = MagicMock()
    mock_models_resp.data = []
    mock_client.models.list = AsyncMock(return_value=mock_models_resp)

    # Close
    mock_client.close = AsyncMock()

    return mock_client


def _make_mock_http_client(response_json=None, *, raise_for_status=None, post_side_effect=None):
    """Create a MagicMock httpx client with a proper async post method."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = response_json or {}
    if raise_for_status is not None:
        mock_response.raise_for_status.side_effect = raise_for_status
    else:
        mock_response.raise_for_status = MagicMock()

    client = MagicMock()
    if post_side_effect is not None:
        client.post = AsyncMock(side_effect=post_side_effect)
    else:
        client.post = AsyncMock(return_value=mock_response)
    return client, client.post


# ---------------------------------------------------------------------------
# parse_sse_stream / parse_sse_stream_full
# ---------------------------------------------------------------------------


class TestParseSseStream:
    """SSE 字节块拼接与解析."""

    def test_simple_text_content(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":" World"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        assert parse_sse_stream(chunks) == "Hello World"

    def test_empty_chunks(self) -> None:
        assert parse_sse_stream([]) == ""

    def test_no_content_deltas(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        assert parse_sse_stream(chunks) == ""

    def test_full_tool_calls(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"get_","arguments":""}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"weather","arguments":"{}"}}]}}]}\n\n',
            b'data: {"choices":[{"finish_reason":"tool_calls"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        result = parse_sse_stream_full(chunks)
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "call_1"
        assert result.tool_calls[0]["function"]["name"] == "get_weather"
        assert result.tool_calls[0]["function"]["arguments"] == "{}"
        assert result.finish_reason == "tool_calls"

    def test_multiple_tool_calls(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"tool_a","arguments":""}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"c2","type":"function","function":{"name":"tool_b","arguments":"{}"}}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        result = parse_sse_stream_full(chunks)
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0]["function"]["name"] == "tool_a"
        assert result.tool_calls[1]["function"]["name"] == "tool_b"

    def test_tool_calls_without_text(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"fn","arguments":"{\\\"q\\\":1}"}}]}}]}\n\n',
            b'data: {"choices":[{"finish_reason":"tool_calls"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        result = parse_sse_stream_full(chunks)
        assert result.text == ""
        assert result.tool_calls is not None
        assert result.tool_calls[0]["function"]["name"] == "fn"

    def test_malformed_json_line_skipped(self) -> None:
        chunks = [
            b"data: not json at all\n\n",
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        assert parse_sse_stream(chunks) == "ok"

    def test_non_data_lines_ignored(self) -> None:
        chunks = [
            b"event: message\n\n",
            b"retry: 5000\n\n",
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        assert parse_sse_stream(chunks) == "hi"

    def test_fragmented_across_chunks(self) -> None:
        """HTTP chunk 边界可能切在 JSON 中间."""
        full_line = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        mid = len(full_line) // 2
        chunks = [full_line[:mid], full_line[mid:]]
        assert parse_sse_stream(chunks) == "Hello"


# ---------------------------------------------------------------------------
# StreamResult
# ---------------------------------------------------------------------------


class TestStreamResult:
    def test_defaults(self) -> None:
        r = StreamResult(text="hi", tool_calls=None, finish_reason=None)
        assert r.text == "hi"
        assert r.tool_calls is None
        assert r.finish_reason is None


# ---------------------------------------------------------------------------
# UpstreamError / UpstreamTimeout
# ---------------------------------------------------------------------------


class TestUpstreamExceptions:
    def test_upstream_error_attributes(self) -> None:
        e = UpstreamError(status_code=502, message="bad gateway")
        assert e.status_code == 502
        assert e.message == "bad gateway"
        assert "502" in str(e)

    def test_upstream_error_no_status(self) -> None:
        e = UpstreamError()
        assert e.status_code is None

    def test_upstream_timeout(self) -> None:
        e = UpstreamTimeout("timed out")
        assert "timed out" in str(e)


# ---------------------------------------------------------------------------
# ForwarderConfig
# ---------------------------------------------------------------------------


class TestForwarderConfig:
    def test_defaults(self) -> None:
        cfg = ForwarderConfig(base_url="http://x", api_key="k")
        assert cfg.default_model == ""
        assert cfg.timeout == 60.0
        assert cfg.connect_timeout == 10.0


# ---------------------------------------------------------------------------
# Forwarder._headers
# ---------------------------------------------------------------------------


class TestForwarderHeaders:
    def test_auth_header(self) -> None:
        cfg = ForwarderConfig(base_url="http://x", api_key="my-key")
        fwd = Forwarder(cfg)
        h = fwd._headers()
        assert h["Authorization"] == "Bearer my-key"
        assert h["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Forwarder.chat (mocked openai SDK)
# ---------------------------------------------------------------------------


class TestForwarderChat:
    @pytest.mark.asyncio
    async def test_chat_success(self) -> None:
        cfg = ForwarderConfig(base_url="http://upstream", api_key="key", default_model="m1")
        fwd = Forwarder(cfg)
        mock_client = _make_mock_openai_client(
            chat_result={"choices": [{"message": {"content": "hi"}}]}
        )
        fwd._openai_client = mock_client

        result = await fwd.chat(messages=[{"role": "user", "content": "hello"}])
        assert result == {"choices": [{"message": {"content": "hi"}}]}
        mock_client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_includes_model_from_arg(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k", default_model="default")
        fwd = Forwarder(cfg)
        mock_client = _make_mock_openai_client(chat_result={})
        fwd._openai_client = mock_client

        await fwd.chat(messages=[], model="override-model")
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "override-model"

    @pytest.mark.asyncio
    async def test_chat_api_status_error_raises_upstream_error(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "server error"
        api_err = APIStatusError("500", response=mock_resp, body=None)

        mock_client = _make_mock_openai_client(chat_side_effect=api_err)
        fwd._openai_client = mock_client

        with pytest.raises(UpstreamError) as exc_info:
            await fwd.chat(messages=[])
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_chat_timeout_raises_upstream_timeout(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)

        timeout_err = APITimeoutError(request=MagicMock())
        mock_client = _make_mock_openai_client(chat_side_effect=timeout_err)
        fwd._openai_client = mock_client

        with pytest.raises(UpstreamTimeout):
            await fwd.chat(messages=[])

    @pytest.mark.asyncio
    async def test_chat_tools_and_extra_body(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)
        mock_client = _make_mock_openai_client(chat_result={})
        fwd._openai_client = mock_client

        tools = [{"type": "function", "function": {"name": "test"}}]
        await fwd.chat(messages=[], tools=tools, tool_choice="auto",
                       response_format={"type": "json_object"},
                       extra_body={"enable_thinking": False})
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["tools"] == tools
        assert call_kwargs["tool_choice"] == "auto"
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["extra_body"] == {"enable_thinking": False}


# ---------------------------------------------------------------------------
# Forwarder.embed (mocked openai SDK)
# ---------------------------------------------------------------------------


class TestForwarderEmbed:
    @pytest.mark.asyncio
    async def test_embed_returns_vectors(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)
        mock_client = _make_mock_openai_client(
            embed_result={"data": [
                {"embedding": [0.1, 0.2], "index": 0},
                {"embedding": [0.3, 0.4], "index": 1},
            ]}
        )
        fwd._openai_client = mock_client

        result = await fwd.embed(input=["a", "b"], model="emb-model")
        assert result == [[0.1, 0.2], [0.3, 0.4]]

    @pytest.mark.asyncio
    async def test_embed_with_dimensions(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)
        mock_client = _make_mock_openai_client(
            embed_result={"data": [{"embedding": [0.1], "index": 0}]}
        )
        fwd._openai_client = mock_client

        await fwd.embed(input="text", model="m", dimensions=512)
        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["dimensions"] == 512

    @pytest.mark.asyncio
    async def test_embed_api_status_error(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"
        api_err = APIStatusError("400", response=mock_resp, body=None)

        mock_client = _make_mock_openai_client(embed_side_effect=api_err)
        fwd._openai_client = mock_client

        with pytest.raises(UpstreamError):
            await fwd.embed(input="x", model="m")


# ---------------------------------------------------------------------------
# Forwarder.rerank (still uses httpx)
# ---------------------------------------------------------------------------


class TestForwarderRerank:
    @pytest.mark.asyncio
    async def test_rerank_success(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)
        client, _ = _make_mock_http_client({
            "results": [{"index": 0, "relevance_score": 0.95}]
        })
        fwd._client = client

        result = await fwd.rerank(query="q", documents=["doc1"], model="m")
        assert len(result) == 1
        assert result[0]["relevance_score"] == 0.95

    @pytest.mark.asyncio
    async def test_rerank_fallback_endpoint(self) -> None:
        """如果 /rerank 返回 404, 应尝试 /reranks."""
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)

        mock_resp_404 = MagicMock()
        mock_resp_404.status_code = 404
        http_err_404 = httpx.HTTPStatusError("404", request=MagicMock(), response=mock_resp_404)

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {"results": [{"index": 0, "relevance_score": 0.8}]}
        mock_resp_ok.raise_for_status = MagicMock()

        client = MagicMock()
        client.post = AsyncMock(side_effect=[http_err_404, mock_resp_ok])
        fwd._client = client

        result = await fwd.rerank(query="q", documents=["d"], model="m")
        assert len(result) == 1
        assert client.post.call_count == 2


# ---------------------------------------------------------------------------
# Forwarder.close
# ---------------------------------------------------------------------------


class TestForwarderClose:
    @pytest.mark.asyncio
    async def test_close_without_pool(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)
        mock_openai = AsyncMock()
        mock_http = AsyncMock()
        fwd._openai_client = mock_openai
        fwd._client = mock_http
        await fwd.close()
        mock_openai.close.assert_called_once()
        mock_http.aclose.assert_called_once()
        assert fwd._openai_client is None
        assert fwd._client is None

    @pytest.mark.asyncio
    async def test_close_with_pool_does_not_close_http_client(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        pool = MagicMock()
        fwd = Forwarder(cfg, pool=pool)
        mock_openai = AsyncMock()
        mock_http = AsyncMock()
        fwd._openai_client = mock_openai
        fwd._client = mock_http
        await fwd.close()
        mock_openai.close.assert_called_once()
        mock_http.aclose.assert_not_called()
        assert fwd._client is mock_http

    @pytest.mark.asyncio
    async def test_close_when_no_client(self) -> None:
        cfg = ForwarderConfig(base_url="http://u", api_key="k")
        fwd = Forwarder(cfg)
        await fwd.close()  # no error


# ---------------------------------------------------------------------------
# _should_fallback (from multi.py)
# ---------------------------------------------------------------------------


class TestShouldFallback:
    def test_timeout_is_fallback(self) -> None:
        assert _should_fallback(UpstreamTimeout("t")) is True

    def test_5xx_error_is_fallback(self) -> None:
        assert _should_fallback(UpstreamError(502, "bad")) is True

    def test_none_status_is_fallback(self) -> None:
        assert _should_fallback(UpstreamError(None, "")) is True

    def test_4xx_error_not_fallback(self) -> None:
        assert _should_fallback(UpstreamError(400, "bad")) is False
        assert _should_fallback(UpstreamError(401, "unauth")) is False
        assert _should_fallback(UpstreamError(429, "rate")) is False

    def test_connect_error_is_fallback(self) -> None:
        assert _should_fallback(httpx.ConnectError("refused")) is True

    def test_read_error_is_fallback(self) -> None:
        assert _should_fallback(httpx.ReadError("truncated")) is True

    def test_generic_exception_not_fallback(self) -> None:
        assert _should_fallback(ValueError("no")) is False
