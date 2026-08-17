"""AnthropicForwarder SDK 调用路径测试 (技术债 T7).

覆盖此前未测的 chat / chat_stream / close / tool_choice 转换 /
流式事件转换全分支 (content_block_start / input_json_delta / message_delta /
空 delta 分支) / 三类上游异常映射.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.infra.forwarder.anthropic import (
    AnthropicForwarder,
    AnthropicForwarderConfig,
    _convert_stream_event,
    _convert_tool_choice,
)
from src.infra.forwarder.forwarder import UpstreamError, UpstreamTimeout


def _cfg(**kw) -> AnthropicForwarderConfig:
    return AnthropicForwarderConfig(
        base_url="https://api.example.com",
        api_key="sk-test",
        default_model="claude-3",
        timeout=5.0,
        **kw,
    )


# ---------------------------------------------------------------------------
# _convert_tool_choice
# ---------------------------------------------------------------------------


class TestConvertToolChoice:
    def test_string_auto(self) -> None:
        assert _convert_tool_choice("auto") == {"type": "auto"}

    def test_string_none(self) -> None:
        assert _convert_tool_choice("none") == {"type": "none"}

    def test_string_required_maps_to_any(self) -> None:
        assert _convert_tool_choice("required") == {"type": "any"}

    def test_dict_function_maps_to_tool(self) -> None:
        assert _convert_tool_choice({"type": "function", "function": {"name": "get_weather"}}) == {
            "type": "tool",
            "name": "get_weather",
        }

    def test_dict_non_function_falls_back_auto(self) -> None:
        assert _convert_tool_choice({"type": "unknown"}) == {"type": "auto"}

    def test_unknown_string_falls_back_auto(self) -> None:
        assert _convert_tool_choice("what") == {"type": "auto"}


# ---------------------------------------------------------------------------
# _convert_stream_event 全分支
# ---------------------------------------------------------------------------


def _event(**kw) -> SimpleNamespace:
    return SimpleNamespace(**kw)


class TestConvertStreamEvent:
    def test_content_block_start_tool_use(self) -> None:
        ev = _event(
            type="content_block_start",
            content_block=_event(type="tool_use", id="tu_1", name="get_weather"),
        )
        chunk = _convert_stream_event(ev, "cm-1", "m")
        assert chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "get_weather"
        assert chunk["choices"][0]["delta"]["tool_calls"][0]["id"] == "tu_1"

    def test_content_block_start_non_tool_returns_none(self) -> None:
        ev = _event(type="content_block_start", content_block=_event(type="text"))
        assert _convert_stream_event(ev, "cm-1", "m") is None

    def test_content_block_start_no_block_returns_none(self) -> None:
        ev = _event(type="content_block_start", content_block=None)
        assert _convert_stream_event(ev, "cm-1", "m") is None

    def test_text_delta(self) -> None:
        ev = _event(type="content_block_delta", delta=_event(type="text_delta", text="你好"))
        chunk = _convert_stream_event(ev, "cm-1", "m")
        assert chunk["choices"][0]["delta"]["content"] == "你好"

    def test_text_delta_empty_returns_none(self) -> None:
        ev = _event(type="content_block_delta", delta=_event(type="text_delta", text=""))
        assert _convert_stream_event(ev, "cm-1", "m") is None

    def test_thinking_delta_empty_returns_none(self) -> None:
        ev = _event(type="content_block_delta", delta=_event(type="thinking_delta", thinking=""))
        assert _convert_stream_event(ev, "cm-1", "m") is None

    def test_input_json_delta(self) -> None:
        ev = _event(
            type="content_block_delta", delta=_event(type="input_json_delta", partial_json='{"c')
        )
        chunk = _convert_stream_event(ev, "cm-1", "m")
        args = chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
        assert args == '{"c'

    def test_delta_none_returns_none(self) -> None:
        ev = _event(type="content_block_delta", delta=None)
        assert _convert_stream_event(ev, "cm-1", "m") is None

    def test_unknown_delta_type_returns_none(self) -> None:
        ev = _event(type="content_block_delta", delta=_event(type="mystery", x=1))
        assert _convert_stream_event(ev, "cm-1", "m") is None

    def test_message_delta_finish_reason(self) -> None:
        ev = _event(type="message_delta", delta=_event(stop_reason="end_turn"))
        chunk = _convert_stream_event(ev, "cm-1", "m")
        assert chunk["choices"][0]["finish_reason"] == "stop"

    def test_message_delta_tool_use(self) -> None:
        ev = _event(type="message_delta", delta=_event(stop_reason="tool_use"))
        chunk = _convert_stream_event(ev, "cm-1", "m")
        assert chunk["choices"][0]["finish_reason"] == "tool_calls"

    def test_message_delta_no_delta(self) -> None:
        ev = _event(type="message_delta", delta=None)
        chunk = _convert_stream_event(ev, "cm-1", "m")
        assert chunk["choices"][0]["finish_reason"] == "stop"

    def test_unknown_event_returns_none(self) -> None:
        assert _convert_stream_event(_event(type="ping"), "cm-1", "m") is None


# ---------------------------------------------------------------------------
# AnthropicForwarder.chat — SDK 调用 + 错误映射
# ---------------------------------------------------------------------------


class TestAnthropicForwarderChat:
    @pytest.mark.asyncio
    async def test_chat_success(self) -> None:
        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=SimpleNamespace(
                id="msg_1",
                content=[SimpleNamespace(type="text", text="你好")],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=3, output_tokens=5),
            )
        )
        fwd = AnthropicForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.anthropic._emit_debug"):
            result = await fwd.chat(
                [{"role": "user", "content": "hi"}],
                tools=None,
                tool_choice="auto",
            )

        assert result["choices"][0]["message"]["content"] == "你好"
        call_kwargs = client.messages.create.await_args.kwargs
        assert call_kwargs["model"] == "claude-3"
        assert call_kwargs["max_tokens"] == 4096
        assert call_kwargs["tool_choice"] == {"type": "auto"}

    @pytest.mark.asyncio
    async def test_chat_timeout_maps_to_upstream_timeout(self) -> None:
        from anthropic import APITimeoutError

        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=APITimeoutError("slow"))
        fwd = AnthropicForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.anthropic._emit_debug"):
            with pytest.raises(UpstreamTimeout):
                await fwd.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_connection_error_maps_to_upstream_error(self) -> None:
        from anthropic import APIConnectionError

        client = AsyncMock()
        client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock(), message="conn")
        )
        fwd = AnthropicForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.anthropic._emit_debug"):
            with pytest.raises(UpstreamError):
                await fwd.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_status_error_maps_to_upstream_error_with_code(self) -> None:
        from anthropic import APIStatusError

        client = AsyncMock()
        err = APIStatusError.__new__(APIStatusError)
        err.status_code = 429
        err.response = SimpleNamespace(text="rate limited")
        client.messages.create = AsyncMock(side_effect=err)
        fwd = AnthropicForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.anthropic._emit_debug"):
            with pytest.raises(UpstreamError) as ei:
                await fwd.chat([{"role": "user", "content": "hi"}])
        assert ei.value.status_code == 429


# ---------------------------------------------------------------------------
# AnthropicForwarder.chat_stream — SSE 透传 + 错误映射
# ---------------------------------------------------------------------------


class TestAnthropicForwarderChatStream:
    @pytest.mark.asyncio
    async def test_stream_yields_sse_and_done(self) -> None:
        client = AsyncMock()
        stream_mgr = AsyncMock()
        stream_mgr.__aenter__ = AsyncMock(return_value=stream_mgr)
        stream_mgr.__aexit__ = AsyncMock(return_value=False)

        async def _events():
            for ev in [
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text="你好"),
                ),
                SimpleNamespace(
                    type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")
                ),
            ]:
                yield ev

        stream_mgr.__aiter__ = lambda self: _events()
        client.messages.stream = MagicMock(return_value=stream_mgr)
        fwd = AnthropicForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.anthropic._emit_debug"):
            chunks = [c async for c in fwd.chat_stream([{"role": "user", "content": "hi"}])]

        assert len(chunks) == 3
        assert "你好".encode() in chunks[0]
        assert chunks[-1] == b"data: [DONE]\n\n"

    @pytest.mark.asyncio
    async def test_stream_connection_error_raises(self) -> None:
        from anthropic import APIConnectionError

        client = AsyncMock()
        err = APIConnectionError(request=MagicMock(), message="conn")
        stream_mgr = AsyncMock()
        stream_mgr.__aenter__ = AsyncMock(side_effect=err)
        stream_mgr.__aexit__ = AsyncMock(return_value=False)
        client.messages.stream = MagicMock(return_value=stream_mgr)
        fwd = AnthropicForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.anthropic._emit_debug"):
            with pytest.raises(UpstreamError):
                async for _ in fwd.chat_stream([{"role": "user", "content": "hi"}]):
                    pass

    @pytest.mark.asyncio
    async def test_close_resets_client(self) -> None:
        client = AsyncMock()
        fwd = AnthropicForwarder(_cfg())
        fwd._client = client
        await fwd.close()
        client.close.assert_awaited_once()
        assert fwd._client is None

    @pytest.mark.asyncio
    async def test_client_lazy_initialized(self) -> None:
        fwd = AnthropicForwarder(_cfg())
        with patch("src.infra.forwarder.anthropic.AsyncAnthropic") as fak:
            client = fwd._get_client()
            fak.assert_called_once_with(
                api_key="sk-test",
                base_url="https://api.example.com",
                timeout=5.0,
                max_retries=0,
            )
            assert client is fak.return_value
            # 二次调用复用同一实例
            assert fwd._get_client() is client
