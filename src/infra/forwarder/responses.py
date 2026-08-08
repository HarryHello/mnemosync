"""OpenAI Responses API 格式转发器.

使用 openai SDK 的 Responses API 调用上游,
自动进行 Chat Completions ↔ Responses API 格式转换.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from .debug_hook import get_debug_bus
from .debug_utils import emit_upstream_debug as _emit_debug
from .forwarder import UpstreamError, UpstreamTimeout

logger = logging.getLogger(__name__)


@dataclass
class ResponsesForwarderConfig:
    """Responses API 转发器配置."""

    base_url: str
    api_key: str
    default_model: str = ""
    timeout: float = 60.0


def _convert_chat_to_responses(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将 Chat Completions 格式转换为 Responses API 格式."""
    # 构建 input 数组
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            # system → instructions 参数, 不在 input 中
            continue

        if role == "user":
            if isinstance(content, str):
                input_items.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # content parts
                input_content = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            input_content.append({"type": "input_text", "text": part.get("text", "")})
                        elif part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            input_content.append({"type": "input_image", "image_url": url})
                if input_content:
                    input_items.append({"role": "user", "content": input_content})

        elif role == "assistant":
            if isinstance(content, str):
                input_items.append({"role": "assistant", "content": content})

        elif role == "tool":
            # tool result → function_call_output
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": content if isinstance(content, str) else json.dumps(content),
            })

    # 提取 system 指令
    instructions = None
    for msg in messages:
        if msg.get("role") == "system":
            instructions = msg.get("content", "")
            break

    result: dict[str, Any] = {"input": input_items}
    if instructions:
        result["instructions"] = instructions

    # tools 转换
    if tools:
        responses_tools = []
        for tool in tools:
            func = tool.get("function", {})
            responses_tools.append({
                "type": "function",
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {}),
            })
        result["tools"] = responses_tools

    return result


def _convert_responses_to_chat(
    response: Any,
    model: str,
) -> dict[str, Any]:
    """将 Responses API 响应转换为 Chat Completions 格式."""
    output = response.output if hasattr(response, "output") else []
    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for item in output:
        item_type = getattr(item, "type", "")
        if item_type == "message":
            # 文本消息
            for content in getattr(item, "content", []):
                if getattr(content, "type", "") == "output_text":
                    text_parts.append(getattr(content, "text", ""))
        elif item_type == "function_call":
            # 工具调用
            tool_calls.append({
                "id": getattr(item, "call_id", ""),
                "type": "function",
                "function": {
                    "name": getattr(item, "name", ""),
                    "arguments": getattr(item, "arguments", "{}"),
                },
            })

    message: dict[str, Any] = {"role": "assistant"}
    if text_parts:
        message["content"] = "\n".join(text_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls

    finish_reason = "stop"
    if tool_calls:
        finish_reason = "tool_calls"

    usage = getattr(response, "usage", None)
    usage_dict = {}
    if usage:
        usage_dict = {
            "prompt_tokens": getattr(usage, "input_tokens", 0),
            "completion_tokens": getattr(usage, "output_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }

    return {
        "id": getattr(response, "id", ""),
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": usage_dict,
    }


class ResponsesForwarder:
    """Responses API 格式上游转发器.

    使用 openai SDK 的 Responses API 调用上游,
    自动进行 Chat Completions ↔ Responses API 格式转换.
    """

    def __init__(self, config: ResponsesForwarderConfig):
        self.config = config
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                max_retries=0,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """非流式对话. 将 Chat Completions 格式转为 Responses API 格式调用."""
        client = self._get_client()
        resolved_model = model or self.config.default_model
        api_url = f"{self.config.base_url}/v1/responses"

        # 转换格式
        responses_body = _convert_chat_to_responses(messages, tools, tool_choice)

        sdk_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "input": responses_body["input"],
        }
        if "instructions" in responses_body:
            sdk_kwargs["instructions"] = responses_body["instructions"]
        if "tools" in responses_body:
            sdk_kwargs["tools"] = responses_body["tools"]
        if temperature is not None:
            sdk_kwargs["temperature"] = temperature

        _emit_debug("upstream_request", api_url, method="POST", body=sdk_kwargs)
        started = time.time()

        try:
            response = await client.responses.create(**sdk_kwargs)
            result = _convert_responses_to_chat(response, resolved_model)

            _emit_debug(
                "upstream_response", api_url, method="POST", status=200,
                duration_ms=(time.time() - started) * 1000, body=result,
            )
            return result
        except APITimeoutError as e:
            _emit_debug(
                "upstream_response", api_url, method="POST", status=None,
                duration_ms=(time.time() - started) * 1000, body={"error": str(e)},
            )
            raise UpstreamTimeout(f"responses timeout after {self.config.timeout}s") from e
        except APIConnectionError as e:
            _emit_debug(
                "upstream_response", api_url, method="POST", status=None,
                duration_ms=(time.time() - started) * 1000, body={"error": str(e)},
            )
            raise UpstreamError(None, str(e)) from e
        except APIStatusError as e:
            _emit_debug(
                "upstream_response", api_url, method="POST", status=e.status_code,
                duration_ms=(time.time() - started) * 1000, body={"error": e.response.text},
            )
            raise UpstreamError(e.status_code, e.response.text) from e

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """流式对话. 将 Responses API 流转换为 Chat Completions SSE 格式."""
        client = self._get_client()
        resolved_model = model or self.config.default_model
        api_url = f"{self.config.base_url}/v1/responses"

        responses_body = _convert_chat_to_responses(messages)

        sdk_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "input": responses_body["input"],
            "stream": True,
        }
        if "instructions" in responses_body:
            sdk_kwargs["instructions"] = responses_body["instructions"]
        if temperature is not None:
            sdk_kwargs["temperature"] = temperature

        # 透传 tools
        tools = kwargs.get("tools")
        if tools:
            responses_tools = _convert_chat_to_responses([], tools).get("tools", [])
            if responses_tools:
                sdk_kwargs["tools"] = responses_tools

        event_id = _emit_debug("upstream_request", api_url, method="POST", body=sdk_kwargs)

        bus = get_debug_bus()
        started = time.time()
        chatcmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        try:
            stream = await client.responses.create(**sdk_kwargs)
            async for event in stream:
                # 将 Responses API 事件转为 Chat Completions SSE 格式
                openai_chunk = _convert_stream_event(event, chatcmpl_id, resolved_model)
                if openai_chunk is not None:
                    sse_line = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                    sse_bytes = sse_line.encode("utf-8")
                    if event_id and bus is not None and bus.should_emit():
                        bus.append_stream_chunk(event_id, sse_bytes)
                    yield sse_bytes

            # 发送 [DONE]
            yield b"data: [DONE]\n\n"

            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id, assembled="",
                    status=200, duration_ms=(time.time() - started) * 1000,
                )
        except APITimeoutError as e:
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id, assembled=f"timeout: {e}",
                    status=None, duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamTimeout(f"responses stream timeout after {self.config.timeout}s") from e
        except APIConnectionError as e:
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id, assembled=str(e),
                    status=None, duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamError(None, str(e)) from e
        except APIStatusError as e:
            if event_id and bus is not None:
                bus.finalize_stream(
                    event_id, assembled=e.response.text,
                    status=e.status_code, duration_ms=(time.time() - started) * 1000,
                )
            raise UpstreamError(e.status_code, e.response.text) from e

    async def close(self) -> None:
        """关闭客户端."""
        if self._client:
            await self._client.close()
            self._client = None


def _convert_stream_event(
    event: Any,
    chatcmpl_id: str,
    model: str,
) -> dict[str, Any] | None:
    """将 Responses API 流式事件转换为 Chat Completions SSE chunk 格式."""
    event_type = getattr(event, "type", "")

    if event_type == "response.output_text.delta":
        text = getattr(event, "delta", "")
        if not text:
            return None
        return {
            "id": chatcmpl_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": text},
                "finish_reason": None,
            }],
        }

    if event_type == "response.output_item.done":
        item = getattr(event, "item", None)
        if item and getattr(item, "type", "") == "function_call":
            return {
                "id": chatcmpl_id,
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": getattr(item, "call_id", ""),
                            "type": "function",
                            "function": {
                                "name": getattr(item, "name", ""),
                                "arguments": getattr(item, "arguments", "{}"),
                            },
                        }],
                    },
                    "finish_reason": None,
                }],
            }

    if event_type == "response.completed":
        return {
            "id": chatcmpl_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }

    return None
