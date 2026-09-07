"""ResponsesForwarder SDK 调用路径测试 (技术债 T7).

覆盖: chat / chat_stream / close / 客户端惰性初始化 /
流式事件转换全分支 / 三类上游异常映射.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.infra.forwarder.forwarder import UpstreamError, UpstreamTimeout
from src.infra.forwarder.responses import (
    ResponsesForwarder,
    ResponsesForwarderConfig,
    _convert_stream_event,
)


def _cfg(**kw) -> ResponsesForwarderConfig:
    return ResponsesForwarderConfig(
        base_url="https://api.example.com",
        api_key="sk-test",
        default_model="gpt-5",
        timeout=5.0,
        **kw,
    )


def _ev(**kw) -> SimpleNamespace:
    return SimpleNamespace(**kw)


class TestConvertStreamEvent:
    def test_output_text_delta(self) -> None:
        chunk = _convert_stream_event(_ev(type="response.output_text.delta", delta="hi"), "c1", "m")
        assert chunk["choices"][0]["delta"]["content"] == "hi"

    def test_output_text_delta_empty_none(self) -> None:
        assert (
            _convert_stream_event(_ev(type="response.output_text.delta", delta=""), "c1", "m")
            is None
        )

    def test_reasoning_delta(self) -> None:
        chunk = _convert_stream_event(
            _ev(type="response.reasoning_text.delta", delta="想"), "c1", "m"
        )
        assert chunk["choices"][0]["delta"]["reasoning_content"] == "想"

    def test_reasoning_delta_empty_none(self) -> None:
        assert (
            _convert_stream_event(_ev(type="response.reasoning_text.delta", delta=""), "c1", "m")
            is None
        )

    def test_output_item_added_function_call(self) -> None:
        st: dict[str, object] = {"saw_tool_call": False}
        ev = _ev(
            type="response.output_item.added",
            item=_ev(type="function_call", call_id="fc_1", name="get_weather"),
        )
        chunk = _convert_stream_event(ev, "c1", "m", st)
        assert chunk["choices"][0]["delta"]["tool_calls"][0]["id"] == "fc_1"
        assert st["saw_tool_call"] is True

    def test_output_item_added_non_function_none(self) -> None:
        ev = _ev(type="response.output_item.added", item=_ev(type="message"))
        assert _convert_stream_event(ev, "c1", "m") is None

    def test_output_item_added_no_item_none(self) -> None:
        assert (
            _convert_stream_event(_ev(type="response.output_item.added", item=None), "c1", "m")
            is None
        )

    def test_function_call_arguments_delta(self) -> None:
        chunk = _convert_stream_event(
            _ev(type="response.function_call_arguments.delta", delta='{"city"'), "c1", "m"
        )
        args = chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
        assert args == '{"city"'

    def test_function_call_arguments_delta_empty_none(self) -> None:
        assert (
            _convert_stream_event(
                _ev(type="response.function_call_arguments.delta", delta=""), "c1", "m"
            )
            is None
        )

    def test_completed_no_tool_stop(self) -> None:
        chunk = _convert_stream_event(_ev(type="response.completed"), "c1", "m")
        assert chunk["choices"][0]["finish_reason"] == "stop"

    def test_completed_with_tool_tool_calls(self) -> None:
        st = {"saw_tool_call": True}
        chunk = _convert_stream_event(_ev(type="response.completed"), "c1", "m", st)
        assert chunk["choices"][0]["finish_reason"] == "tool_calls"

    def test_unknown_event_none(self) -> None:
        assert _convert_stream_event(_ev(type="response.whatever"), "c1", "m") is None


class TestResponsesForwarderChat:
    @pytest.mark.asyncio
    async def test_chat_success(self) -> None:
        client = AsyncMock()
        client.responses.create = AsyncMock(
            return_value=SimpleNamespace(
                id="resp_1",
                output=[
                    SimpleNamespace(
                        type="message", content=[SimpleNamespace(type="output_text", text="你好")]
                    )
                ],
                usage=None,
            )
        )
        fwd = ResponsesForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.responses._emit_debug"):
            result = await fwd.chat(
                [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}],
                model="gpt-5",
                temperature=0.7,
            )

        assert result["choices"][0]["message"]["content"] == "你好"
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["instructions"] == "你是助手"
        assert kwargs["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_chat_timeout_maps_to_upstream_timeout(self) -> None:
        from openai import APITimeoutError

        client = AsyncMock()
        client.responses.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        fwd = ResponsesForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.responses._emit_debug"):
            with pytest.raises(UpstreamTimeout):
                await fwd.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_connection_error_maps_to_upstream_error(self) -> None:
        from openai import APIConnectionError

        client = AsyncMock()
        client.responses.create = AsyncMock(
            side_effect=APIConnectionError(message="conn", request=MagicMock())
        )
        fwd = ResponsesForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.responses._emit_debug"):
            with pytest.raises(UpstreamError):
                await fwd.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_status_error_maps_to_upstream_error_with_code(self) -> None:
        from openai import APIStatusError

        client = AsyncMock()
        err = APIStatusError.__new__(APIStatusError)
        err.status_code = 429
        err.response = SimpleNamespace(text="rate limited")
        client.responses.create = AsyncMock(side_effect=err)
        fwd = ResponsesForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.responses._emit_debug"):
            with pytest.raises(UpstreamError) as ei:
                await fwd.chat([{"role": "user", "content": "hi"}])
        assert ei.value.status_code == 429


class TestResponsesForwarderChatStream:
    @pytest.mark.asyncio
    async def test_stream_yields_sse_and_done(self) -> None:
        client = AsyncMock()

        async def _events():
            for ev in [
                _ev(type="response.output_text.delta", delta="你好"),
                _ev(type="response.completed"),
            ]:
                yield ev

        client.responses.create = AsyncMock(return_value=_events())
        fwd = ResponsesForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.responses._emit_debug"):
            chunks = [c async for c in fwd.chat_stream([{"role": "user", "content": "hi"}])]

        assert len(chunks) == 3
        assert "你好".encode() in chunks[0]
        assert chunks[-1] == b"data: [DONE]\n\n"
        assert client.responses.create.await_args.kwargs["stream"] is True

    @pytest.mark.asyncio
    async def test_stream_connection_error_raises(self) -> None:
        from openai import APIConnectionError

        client = AsyncMock()
        client.responses.create = AsyncMock(
            side_effect=APIConnectionError(message="conn", request=MagicMock())
        )
        fwd = ResponsesForwarder(_cfg())
        fwd._client = client

        with patch("src.infra.forwarder.responses._emit_debug"):
            with pytest.raises(UpstreamError):
                async for _ in fwd.chat_stream([{"role": "user", "content": "hi"}]):
                    pass

    @pytest.mark.asyncio
    async def test_close_resets_client(self) -> None:
        client = AsyncMock()
        fwd = ResponsesForwarder(_cfg())
        fwd._client = client
        await fwd.close()
        client.close.assert_awaited_once()
        assert fwd._client is None

    @pytest.mark.asyncio
    async def test_client_lazy_initialized(self) -> None:
        fwd = ResponsesForwarder(_cfg())
        with patch("src.infra.forwarder.responses.AsyncOpenAI") as fak:
            client = fwd._get_client()
            fak.assert_called_once_with(
                api_key="sk-test",
                base_url="https://api.example.com",
                timeout=5.0,
                max_retries=0,
            )
            assert client is fak.return_value
            assert fwd._get_client() is client
