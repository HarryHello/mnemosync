"""Anthropic Messages API 下游适配器.

提供 POST /v1/messages 端点, 接受 Anthropic Messages API 格式请求,
转换为内部 OpenAI 格式处理, 再转换回 Anthropic 格式响应.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.api.schemas.forward import ChatCompletionRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


# ── 请求/响应 Schema ──────────────────────────────────────────


class AnthropicContentBlock(BaseModel):
    type: str
    text: str | None = None
    source: dict[str, Any] | None = None
    tool_use_id: str | None = None
    content: str | list[dict[str, Any]] | None = None


class AnthropicMessage(BaseModel):
    role: str
    content: str | list[AnthropicContentBlock]


class AnthropicTool(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class AnthropicMessagesRequest(BaseModel):
    model: str = "mnemosync-any"
    messages: list[AnthropicMessage]
    system: str | list[dict[str, Any]] | None = None
    max_tokens: int = 4096
    temperature: float = 1.0
    stream: bool = False
    tools: list[AnthropicTool] | None = None
    tool_choice: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


# ── 格式转换 ──────────────────────────────────────────────────


def _convert_anthropic_to_openai(body: AnthropicMessagesRequest) -> dict[str, Any]:
    """将 Anthropic Messages API 请求转换为 OpenAI Chat Completions 格式."""
    messages: list[dict[str, Any]] = []

    # system → system message
    if body.system:
        if isinstance(body.system, str):
            messages.append({"role": "system", "content": body.system})
        elif isinstance(body.system, list):
            # Anthropic system 可以是 content blocks 数组
            texts = [b.get("text", "") for b in body.system if isinstance(b, dict) and b.get("type") == "text"]
            messages.append({"role": "system", "content": "\n".join(texts)})

    # messages → OpenAI messages
    for msg in body.messages:
        if isinstance(msg.content, str):
            messages.append({"role": msg.role, "content": msg.content})
        elif isinstance(msg.content, list):
            # Anthropic content blocks → OpenAI content parts
            openai_content: list[dict[str, Any]] = []
            for block in msg.content:
                if block.type == "text":
                    openai_content.append({"type": "text", "text": block.text or ""})
                elif block.type == "image":
                    # Anthropic image → OpenAI image_url
                    source = block.source or {}
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        data = source.get("data", "")
                        openai_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"},
                        })
                    elif source.get("type") == "url":
                        openai_content.append({
                            "type": "image_url",
                            "image_url": {"url": source.get("url", "")},
                        })
                elif block.type == "tool_use":
                    # 工具调用结果应该在 assistant 消息中
                    pass
                elif block.type == "tool_result":
                    # tool_result → tool message
                    messages.append({
                        "role": "tool",
                        "tool_call_id": block.tool_use_id or "",
                        "content": block.content if isinstance(block.content, str) else json.dumps(block.content),
                    })

            if openai_content:
                messages.append({"role": msg.role, "content": openai_content})
            elif not any(b.type == "tool_result" for b in msg.content):
                messages.append({"role": msg.role, "content": ""})

    # tools
    openai_tools = None
    if body.tools:
        openai_tools = []
        for tool in body.tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            })

    # tool_choice
    openai_tool_choice: str | dict[str, Any] = "auto"
    if body.tool_choice:
        tc_type = body.tool_choice.get("type", "auto")
        if tc_type == "auto":
            openai_tool_choice = "auto"
        elif tc_type == "any":
            openai_tool_choice = "required"
        elif tc_type == "tool":
            openai_tool_choice = {
                "type": "function",
                "function": {"name": body.tool_choice.get("name", "")},
            }
        elif tc_type == "none":
            openai_tool_choice = "none"

    result: dict[str, Any] = {
        "model": body.model,
        "messages": messages,
        "max_tokens": body.max_tokens,
        "temperature": body.temperature,
        "stream": body.stream,
    }
    if openai_tools:
        result["tools"] = openai_tools
    if openai_tool_choice is not None:
        result["tool_choice"] = openai_tool_choice

    return result


def _convert_openai_to_anthropic_response(
    openai_response: dict[str, Any],
) -> dict[str, Any]:
    """将 OpenAI Chat Completions 响应转换为 Anthropic Messages 格式."""
    choices = openai_response.get("choices", [])
    if not choices:
        return {"type": "error", "error": {"type": "api_error", "message": "No choices in response"}}

    choice = choices[0]
    message = choice.get("message", {})
    content_parts: list[dict[str, Any]] = []

    # 文本内容
    text = message.get("content", "")
    if text:
        content_parts.append({"type": "text", "text": text})

    # 工具调用
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        content_parts.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": func.get("name", ""),
            "input": args,
        })

    # stop_reason 映射
    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = "end_turn"
    if finish_reason == "tool_calls":
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"

    usage = openai_response.get("usage", {})

    return {
        "id": openai_response.get("id", ""),
        "type": "message",
        "role": "assistant",
        "content": content_parts,
        "model": openai_response.get("model", ""),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── 流式转换 ──────────────────────────────────────────────────


def _convert_openai_chunk_to_anthropic(
    chunk: dict[str, Any],
    content_block_index: int = 0,
) -> list[dict[str, Any]]:
    """将 OpenAI SSE chunk 转换为 Anthropic SSE 事件列表."""
    events: list[dict[str, Any]] = []
    choices = chunk.get("choices", [])
    if not choices:
        return events

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # 文本 delta
    content = delta.get("content")
    if content:
        events.append({
            "type": "content_block_delta",
            "index": content_block_index,
            "delta": {"type": "text_delta", "text": content},
        })

    # 工具调用 delta
    tool_calls = delta.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        if func.get("name"):
            # 新工具调用开始
            events.append({
                "type": "content_block_start",
                "index": content_block_index + 1,
                "content_block": {
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": func["name"],
                    "input": {},
                },
            })
        if func.get("arguments"):
            events.append({
                "type": "content_block_delta",
                "index": content_block_index + 1,
                "delta": {"type": "input_json_delta", "partial_json": func["arguments"]},
            })

    # finish_reason → message_delta
    if finish_reason:
        stop_reason = "end_turn"
        if finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif finish_reason == "length":
            stop_reason = "max_tokens"
        events.append({
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 0},
        })

    return events


# ── 端点 ──────────────────────────────────────────────────────


@router.post("/messages", tags=["Anthropic"])
async def handle_anthropic_messages(
    request: Request,
    body: AnthropicMessagesRequest,
) -> Any:
    """处理 Anthropic Messages API 格式请求.

    将请求转换为 OpenAI 格式, 调用内部转发管线, 再转换回 Anthropic 格式.
    """
    from src.api.routes.forward import create_chat_completion

    # 转换为 OpenAI 格式
    openai_body = _convert_anthropic_to_openai(body)

    # 构建 ChatCompletionRequest
    chat_request = ChatCompletionRequest(**openai_body)

    # 调用内部管线
    if body.stream:
        # 流式响应
        return await _handle_anthropic_stream(request, chat_request, body)
    else:
        # 非流式响应
        openai_response = await create_chat_completion(chat_request, request)
        if isinstance(openai_response, JSONResponse):
            # 解析 JSON 响应并转换格式
            body_bytes = cast(bytes, openai_response.body)
            response_body = json.loads(body_bytes.decode("utf-8"))
            anthropic_response = _convert_openai_to_anthropic_response(response_body)
            return JSONResponse(content=anthropic_response)
        return openai_response


async def _handle_anthropic_stream(
    request: Request,
    chat_request: ChatCompletionRequest,
    body: AnthropicMessagesRequest,
) -> StreamingResponse:
    """处理 Anthropic 流式请求."""
    from src.api.routes.forward import create_chat_completion

    # 调用内部管线获取 OpenAI 流式响应
    openai_response = await create_chat_completion(chat_request, request)

    if not isinstance(openai_response, StreamingResponse):
        # 非流式响应, 包装为流式
        body_bytes = cast(bytes, openai_response.body)
        response_body = json.loads(body_bytes.decode("utf-8"))
        anthropic_response = _convert_openai_to_anthropic_response(response_body)
        async def _single_event() -> AsyncGenerator[bytes, None]:
            yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': anthropic_response})}\n\n".encode()
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode()
        return StreamingResponse(_single_event(), media_type="text/event-stream")

    # 包装 OpenAI SSE 流为 Anthropic SSE 格式
    async def anthropic_stream() -> AsyncGenerator[bytes, None]:
        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        model = body.model

        # message_start 事件
        yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': message_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model, 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n".encode()

        # content_block_start
        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n".encode()

        # 转发流式数据
        async for chunk_bytes in openai_response.body_iterator:
            chunk_str = cast(bytes, chunk_bytes).decode("utf-8", errors="ignore").strip()
            if not chunk_str or chunk_str == "data: [DONE]":
                continue
            if not chunk_str.startswith("data: "):
                continue

            data = chunk_str[6:]
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            # 转换为 Anthropic 事件
            anthropic_events = _convert_openai_chunk_to_anthropic(chunk)
            for event in anthropic_events:
                event_type = event.get("type", "")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n".encode()

        # content_block_stop
        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n".encode()

        # message_stop
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode()

    return StreamingResponse(anthropic_stream(), media_type="text/event-stream")
