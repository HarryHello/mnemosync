"""OpenAI Responses API 下游适配器.

提供 POST /v1/responses 端点, 接受 OpenAI Responses API 格式请求,
转换为内部 Chat Completions 格式处理, 再转换回 Responses API 格式响应.
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


class ResponsesInputContent(BaseModel):
    type: str
    text: str | None = None
    image_url: str | None = None


class ResponsesInputItem(BaseModel):
    role: str | None = None
    content: str | list[ResponsesInputContent] | None = None
    type: str | None = None
    call_id: str | None = None
    output: str | None = None
    name: str | None = None
    arguments: str | None = None


class ResponsesTool(BaseModel):
    type: str = "function"
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ResponsesRequest(BaseModel):
    model: str = "mnemosync-any"
    input: str | list[ResponsesInputItem]
    instructions: str | None = None
    tools: list[ResponsesTool] | None = None
    stream: bool = False
    temperature: float = 1.0
    max_output_tokens: int | None = None
    metadata: dict[str, Any] | None = None


# ── 格式转换 ──────────────────────────────────────────────────


def _convert_responses_to_chat(body: ResponsesRequest) -> dict[str, Any]:
    """将 Responses API 请求转换为 Chat Completions 格式."""
    messages: list[dict[str, Any]] = []

    # instructions → system message
    if body.instructions:
        messages.append({"role": "system", "content": body.instructions})

    # input → messages
    if isinstance(body.input, str):
        # 简单字符串输入
        messages.append({"role": "user", "content": body.input})
    elif isinstance(body.input, list):
        for item in body.input:
            if item.role == "user":
                if isinstance(item.content, str):
                    messages.append({"role": "user", "content": item.content})
                elif isinstance(item.content, list):
                    openai_content: list[dict[str, Any]] = []
                    for part in item.content:
                        if part.type == "input_text":
                            openai_content.append({"type": "text", "text": part.text or ""})
                        elif part.type == "input_image":
                            openai_content.append({
                                "type": "image_url",
                                "image_url": {"url": part.image_url or ""},
                            })
                    if openai_content:
                        messages.append({"role": "user", "content": openai_content})
                    else:
                        messages.append({"role": "user", "content": ""})

            elif item.role == "assistant":
                if isinstance(item.content, str):
                    messages.append({"role": "assistant", "content": item.content})

            elif item.type == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.call_id or "",
                    "content": item.output or "",
                })

    # tools 转换
    openai_tools = None
    if body.tools:
        openai_tools = []
        for tool in body.tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })

    result: dict[str, Any] = {
        "model": body.model,
        "messages": messages,
        "stream": body.stream,
        "temperature": body.temperature,
    }
    if body.max_output_tokens:
        result["max_tokens"] = body.max_output_tokens
    if openai_tools:
        result["tools"] = openai_tools

    return result


def _convert_chat_to_responses(
    openai_response: dict[str, Any],
) -> dict[str, Any]:
    """将 Chat Completions 响应转换为 Responses API 格式."""
    choices = openai_response.get("choices", [])
    if not choices:
        return {"type": "error", "error": {"type": "api_error", "message": "No choices"}}

    choice = choices[0]
    message = choice.get("message", {})
    output: list[dict[str, Any]] = []

    # 文本内容
    text = message.get("content", "")
    if text:
        output.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
            "status": "completed",
        })

    # 工具调用
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        output.append({
            "type": "function_call",
            "id": f"fc_{uuid.uuid4().hex[:24]}",
            "call_id": tc.get("id", ""),
            "name": func.get("name", ""),
            "arguments": func.get("arguments", "{}"),
            "status": "completed",
        })

    usage = openai_response.get("usage", {})

    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "model": openai_response.get("model", ""),
        "output": output,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "status": "completed",
    }


# ── 流式转换 ──────────────────────────────────────────────────


def _convert_chat_chunk_to_responses(
    chunk: dict[str, Any],
    response_id: str,
) -> list[dict[str, Any]]:
    """将 Chat Completions SSE chunk 转换为 Responses API 事件列表."""
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
            "type": "response.output_text.delta",
            "item_id": f"msg_{response_id}",
            "output_index": 0,
            "content_index": 0,
            "delta": content,
        })

    # 工具调用 delta
    tool_calls = delta.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        if func.get("name"):
            events.append({
                "type": "response.output_item.added",
                "output_index": len(events),
                "item": {
                    "type": "function_call",
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "call_id": tc.get("id", ""),
                    "name": func["name"],
                    "arguments": "",
                    "status": "in_progress",
                },
            })
        if func.get("arguments"):
            events.append({
                "type": "response.function_call_arguments.delta",
                "output_index": len(events) - 1,
                "delta": func["arguments"],
            })

    # finish_reason → completed
    if finish_reason:
        events.append({
            "type": "response.completed",
            "response": {
                "id": response_id,
                "status": "completed",
            },
        })

    return events


# ── 端点 ──────────────────────────────────────────────────────


@router.post("/responses", tags=["Responses API"])
async def handle_responses(
    request: Request,
    body: ResponsesRequest,
) -> Any:
    """处理 OpenAI Responses API 格式请求.

    将请求转换为 Chat Completions 格式, 调用内部转发管线, 再转换回 Responses API 格式.
    """
    from src.api.routes.forward import create_chat_completion

    # 转换为 Chat Completions 格式
    chat_body = _convert_responses_to_chat(body)

    # 构建 ChatCompletionRequest
    chat_request = ChatCompletionRequest(**chat_body)

    if body.stream:
        return await _handle_responses_stream(request, chat_request, body)
    else:
        openai_response = await create_chat_completion(chat_request, request)
        if isinstance(openai_response, JSONResponse):
            body_bytes = cast(bytes, openai_response.body)
            response_body = json.loads(body_bytes.decode("utf-8"))
            responses_result = _convert_chat_to_responses(response_body)
            return JSONResponse(content=responses_result)
        return openai_response


async def _handle_responses_stream(
    request: Request,
    chat_request: ChatCompletionRequest,
    body: ResponsesRequest,
) -> StreamingResponse:
    """处理 Responses API 流式请求."""
    from src.api.routes.forward import create_chat_completion

    openai_response = await create_chat_completion(chat_request, request)

    if not isinstance(openai_response, StreamingResponse):
        body_bytes = cast(bytes, openai_response.body)
        response_body = json.loads(body_bytes.decode("utf-8"))
        responses_result = _convert_chat_to_responses(response_body)
        async def _single_event() -> AsyncGenerator[bytes, None]:
            yield f"data: {json.dumps({'type': 'response.created', 'response': responses_result})}\n\n".encode()
            yield f"data: {json.dumps({'type': 'response.completed', 'response': responses_result})}\n\n".encode()
        return StreamingResponse(_single_event(), media_type="text/event-stream")

    response_id = f"resp_{uuid.uuid4().hex[:24]}"

    async def responses_stream() -> AsyncGenerator[bytes, None]:
        # response.created
        yield f"data: {json.dumps({'type': 'response.created', 'response': {'id': response_id, 'status': 'in_progress'}})}\n\n".encode()

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

            events = _convert_chat_chunk_to_responses(chunk, response_id)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n".encode()

    return StreamingResponse(responses_stream(), media_type="text/event-stream")
