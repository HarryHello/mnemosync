"""Anthropic 格式转发器: 使用 anthropic SDK 调用 Anthropic Messages API.

负责:
- 将内部 OpenAI 格式转换为 Anthropic Messages API 格式
- 调用 Anthropic SDK (非流式 + 流式)
- 将 Anthropic 响应转换回 OpenAI 格式

与 Forwarder 的关系:
- Forwarder 处理 OpenAI 格式的上游调用
- AnthropicForwarder 处理 Anthropic 格式的上游调用
- 两者接口相似, 由 MultiForwarder 根据 api_format 选择
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from anthropic import APIConnectionError, APIStatusError, APITimeoutError, AsyncAnthropic

from .debug_hook import get_debug_bus
from .debug_utils import emit_upstream_debug as _emit_debug
from .forwarder import UpstreamError, UpstreamTimeout

logger = logging.getLogger(__name__)


@dataclass
class AnthropicForwarderConfig:
    """Anthropic 转发器配置."""

    base_url: str
    api_key: str
    default_model: str = ""
    timeout: float = 60.0


def _convert_messages_to_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """将 OpenAI 格式 messages 转换为 Anthropic 格式.

    Returns:
        (system_prompt, anthropic_messages) 元组
    """
    system_prompt: str | None = None
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            # Anthropic 的 system 是顶层参数
            system_prompt = content if isinstance(content, str) else str(content)
            continue

        if role in ("user", "assistant"):
            # 转换 content 格式
            if isinstance(content, str):
                anthropic_messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                # OpenAI content parts → Anthropic content blocks
                anthropic_content = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type", "")
                    if part_type == "text":
                        anthropic_content.append({
                            "type": "text",
                            "text": part.get("text", ""),
                        })
                    elif part_type == "image_url":
                        # Anthropic 图片格式
                        image_url = part.get("image_url", {})
                        url = image_url.get("url", "")
                        if url.startswith("data:"):
                            # base64 图片
                            media_type, data = _parse_data_url(url)
                            anthropic_content.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": data,
                                },
                            })
                        else:
                            # URL 图片
                            anthropic_content.append({
                                "type": "image",
                                "source": {
                                    "type": "url",
                                    "url": url,
                                },
                            })
                if anthropic_content:
                    anthropic_messages.append({"role": role, "content": anthropic_content})
                else:
                    anthropic_messages.append({"role": role, "content": ""})
            else:
                anthropic_messages.append({"role": role, "content": str(content) or ""})

        elif role == "tool":
            # Anthropic 使用 tool_result
            tool_id = msg.get("tool_call_id", "")
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content if isinstance(content, str) else str(content),
                }],
            })

    return system_prompt, anthropic_messages


def _parse_data_url(url: str) -> tuple[str, str]:
    """解析 data:image/xxx;base64,... 格式的 URL."""
    # data:image/png;base64,iVBOR...
    header, data = url.split(",", 1)
    media_type = header.split(":")[1].split(";")[0]
    return media_type, data


def _convert_tools_to_anthropic(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """将 OpenAI 格式 tools 转换为 Anthropic 格式."""
    if not tools:
        return None

    anthropic_tools = []
    for tool in tools:
        func = tool.get("function", {})
        anthropic_tools.append({
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        })
    return anthropic_tools


def _convert_anthropic_response_to_openai(
    response: Any,
    model: str,
) -> dict[str, Any]:
    """将 Anthropic 响应转换为 OpenAI 格式."""
    content_parts = response.content
    text_parts = []
    tool_calls = []

    for part in content_parts:
        if part.type == "text":
            text_parts.append(part.text)
        elif part.type == "tool_use":
            tool_calls.append({
                "id": part.id,
                "type": "function",
                "function": {
                    "name": part.name,
                    "arguments": json.dumps(part.input),
                },
            })

    result: dict[str, Any] = {
        "id": response.id,
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
            },
            "finish_reason": _map_stop_reason(response.stop_reason),
        }],
        "usage": {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        },
    }

    if tool_calls:
        result["choices"][0]["message"]["tool_calls"] = tool_calls

    return result


def _map_stop_reason(reason: str | None) -> str:
    """将 Anthropic stop_reason 映射为 OpenAI finish_reason."""
    if reason == "end_turn":
        return "stop"
    if reason == "max_tokens":
        return "length"
    if reason == "tool_use":
        return "tool_calls"
    return "stop"


class AnthropicForwarder:
    """Anthropic 格式上游转发器.

    使用 anthropic SDK 调用 Anthropic Messages API,
    自动进行 OpenAI ↔ Anthropic 格式转换.
    """

    def __init__(self, config: AnthropicForwarderConfig):
        self.config = config
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(
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
        """非流式对话. 将 OpenAI 格式转为 Anthropic 格式调用."""
        client = self._get_client()
        resolved_model = model or self.config.default_model
        api_url = f"{self.config.base_url}/v1/messages"

        system_prompt, anthropic_messages = _convert_messages_to_anthropic(messages)
        anthropic_tools = _convert_tools_to_anthropic(tools)

        # 构建 SDK 参数
        sdk_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system_prompt:
            sdk_kwargs["system"] = system_prompt
        if anthropic_tools:
            sdk_kwargs["tools"] = anthropic_tools
        # tool_choice 转换
        if tool_choice is not None:
            sdk_kwargs["tool_choice"] = _convert_tool_choice(tool_choice)

        _emit_debug("upstream_request", api_url, method="POST", body=sdk_kwargs)
        started = time.time()

        try:
            response = await client.messages.create(**sdk_kwargs)
            result = _convert_anthropic_response_to_openai(response, resolved_model)

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
            raise UpstreamTimeout(f"anthropic timeout after {self.config.timeout}s") from e
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
        """流式对话. 将 Anthropic SSE 转换为 OpenAI SSE 格式."""
        client = self._get_client()
        resolved_model = model or self.config.default_model
        api_url = f"{self.config.base_url}/v1/messages"

        system_prompt, anthropic_messages = _convert_messages_to_anthropic(messages)

        sdk_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system_prompt:
            sdk_kwargs["system"] = system_prompt

        # 透传 tools
        tools = kwargs.get("tools")
        if tools:
            anthropic_tools = _convert_tools_to_anthropic(tools)
            if anthropic_tools:
                sdk_kwargs["tools"] = anthropic_tools

        event_id = _emit_debug("upstream_request", api_url, method="POST", body=sdk_kwargs)

        bus = get_debug_bus()
        started = time.time()
        chatcmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        try:
            async with client.messages.stream(**sdk_kwargs) as stream:
                async for event in stream:
                    # 将 Anthropic 事件转为 OpenAI SSE 格式
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
            raise UpstreamTimeout(f"anthropic stream timeout after {self.config.timeout}s") from e
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


def _convert_tool_choice(tool_choice: str | dict[str, Any]) -> dict[str, Any]:
    """将 OpenAI tool_choice 转换为 Anthropic 格式."""
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "none":
            return {"type": "none"}
        if tool_choice == "required":
            return {"type": "any"}
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            return {"type": "tool", "name": tool_choice.get("function", {}).get("name", "")}
    return {"type": "auto"}


def _convert_stream_event(
    event: Any,
    chatcmpl_id: str,
    model: str,
) -> dict[str, Any] | None:
    """将 Anthropic 流式事件转换为 OpenAI SSE chunk 格式."""
    event_type = getattr(event, "type", "")

    if event_type == "content_block_start":
        block = getattr(event, "content_block", None)
        if block and getattr(block, "type", "") == "tool_use":
            return {
                "id": chatcmpl_id,
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": getattr(block, "id", ""),
                            "type": "function",
                            "function": {
                                "name": getattr(block, "name", ""),
                                "arguments": "",
                            },
                        }],
                    },
                    "finish_reason": None,
                }],
            }
        return None

    if event_type == "content_block_delta":
        delta = getattr(event, "delta", None)
        if delta is None:
            return None
        delta_type = getattr(delta, "type", "")

        if delta_type == "text_delta":
            text = getattr(delta, "text", "")
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

        if delta_type == "input_json_delta":
            partial_json = getattr(delta, "partial_json", "")
            return {
                "id": chatcmpl_id,
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "function": {"arguments": partial_json},
                        }],
                    },
                    "finish_reason": None,
                }],
            }

    if event_type == "message_delta":
        delta = getattr(event, "delta", None)
        stop_reason = getattr(delta, "stop_reason", None) if delta else None
        finish_reason = _map_stop_reason(stop_reason)
        return {
            "id": chatcmpl_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }],
        }

    return None
