"""OpenAI Responses API 下游适配器.

提供 POST /v1/responses 端点, 接受 OpenAI Responses API 格式请求,
转换为内部 Chat Completions 格式处理, 再转换回 Responses API 格式响应.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.api.schemas.forward import ChatCompletionRequest

logger = logging.getLogger(__name__)

# 无 prefix — 由父 router (forward/__init__.py, prefix="/v1") 拼接, 否则会变成 /v1/v1/responses
router = APIRouter()


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


@dataclass
class _ResponsesStreamState:
    """跨 chunk 的流式状态: 记录已发的 item/part, 保证事件顺序符合协议.

    AI SDK (Cherry Studio) 用 zod 严格校验事件结构:
      - output_item.done 必须带完整 item (message 需含 content, function_call 需含 arguments)
      - response.completed 的 response 必须带 usage (input_tokens/output_tokens)
    """

    item_id: str = ""
    message_item_added: bool = False
    content_part_added: bool = False
    text_done: bool = False
    message_item_done: bool = False
    output_index: int = 0
    content_index: int = 0
    collected_text: list[str] = field(default_factory=list)
    # function_call 追踪 (output_index → item)
    function_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    tool_args: dict[int, str] = field(default_factory=dict)


def _convert_chat_chunk_to_responses(
    chunk: dict[str, Any],
    response_id: str,
    state: _ResponsesStreamState | None = None,
) -> list[dict[str, Any]]:
    """将 Chat Completions SSE chunk 转换为 Responses API 事件列表.

    Responses API 流式协议要求按序发:
      output_item.added (message) → content_part.added (output_text)
      → output_text.delta → output_text.done → output_item.done → completed
    缺 output_item.added / content_part.added 时, 客户端无法建立 part 索引,
    会报 "text part ... not found". 因此转换需要跨 chunk 状态 (state).
    """
    events: list[dict[str, Any]] = []
    if state is None:
        state = _ResponsesStreamState()
    choices = chunk.get("choices", [])
    if not choices:
        return events

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # 文本输出
    content = delta.get("content")
    if content:
        if not state.message_item_added:
            state.item_id = f"msg_{response_id}"
            state.output_index = 0
            events.append({
                "type": "response.output_item.added",
                "output_index": state.output_index,
                "item": {
                    "type": "message",
                    "id": state.item_id,
                    "status": "in_progress",
                    "content": [],
                },
            })
            state.message_item_added = True
        if not state.content_part_added:
            events.append({
                "type": "response.content_part.added",
                "item_id": state.item_id,
                "output_index": state.output_index,
                "content_index": state.content_index,
                "part": {"type": "output_text", "text": "", "annotations": []},
            })
            state.content_part_added = True
        state.collected_text.append(content)
        events.append({
            "type": "response.output_text.delta",
            "item_id": state.item_id,
            "output_index": state.output_index,
            "content_index": state.content_index,
            "delta": content,
        })

    # 工具调用 delta (注意: 键存在但值为 null 时 .get 默认值不生效, 需 or [])
    tool_calls = delta.get("tool_calls") or []
    for tc in tool_calls:
        func = tc.get("function", {})
        idx = tc.get("index", 0) if isinstance(tc.get("index"), int) else state.output_index
        if func.get("name"):
            existing = state.function_calls.get(idx)
            if existing is None:
                item: dict[str, Any] = {
                    "type": "function_call",
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "call_id": tc.get("id", ""),
                    "name": func["name"],
                    "arguments": "",
                    "status": "in_progress",
                }
                state.function_calls[idx] = item
                events.append({
                    "type": "response.output_item.added",
                    "output_index": idx,
                    "item": item,
                })
            else:
                # 工具名跨帧分片, 需累积 ("get_" + "weather" → "get_weather")
                existing["name"] += func["name"]
        if func.get("arguments"):
            state.tool_args[idx] = state.tool_args.get(idx, "") + func["arguments"]
            events.append({
                "type": "response.function_call_arguments.delta",
                "output_index": idx,
                "delta": func["arguments"],
            })

    # finish_reason → 收尾事件序列
    if finish_reason:
        # 文本 item 收尾: output_text.done → output_item.done (带完整 message item)
        if state.message_item_added and not state.text_done:
            full_text = "".join(state.collected_text)
            events.append({
                "type": "response.output_text.done",
                "item_id": state.item_id,
                "output_index": state.output_index,
                "content_index": state.content_index,
                "text": full_text,
            })
            state.text_done = True
        if state.message_item_added and not state.message_item_done:
            events.append({
                "type": "response.output_item.done",
                "output_index": state.output_index,
                "item": {
                    "type": "message",
                    "id": state.item_id,
                    "status": "completed",
                    "content": [{
                        "type": "output_text",
                        "text": "".join(state.collected_text),
                        "annotations": [],
                    }],
                },
            })
            state.message_item_done = True

        # function_call item 收尾 (带完整 arguments)
        for idx, item in state.function_calls.items():
            events.append({
                "type": "response.output_item.done",
                "output_index": idx,
                "item": {
                    **item,
                    "arguments": state.tool_args.get(idx, ""),
                    "status": "completed",
                },
            })

        events.append({
            "type": "response.completed",
            "response": {
                "id": response_id,
                "status": "completed",
                # AI SDK zod 校验必填 usage
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
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
    model = getattr(body, "model", None) or "mnemosync-any"

    async def responses_stream() -> AsyncGenerator[bytes, None]:
        import time as _time

        # 跨 chunk 的流式状态 (保证 output_item.added / content_part.added 先于 delta)
        state = _ResponsesStreamState()
        # 流结束时若上游没给 finish_reason (异常中断), 补完成事件
        completed = False

        # AI SDK zod 校验: response.created / response.in_progress 的 response
        # 必须含 id + created_at(number) + model(string)
        created_at = int(_time.time())
        base_resp = {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "model": model,
            "status": "in_progress",
        }
        yield f"data: {json.dumps({'type': 'response.created', 'response': base_resp})}\n\n".encode()
        yield f"data: {json.dumps({'type': 'response.in_progress', 'response': base_resp})}\n\n".encode()

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

            events = _convert_chat_chunk_to_responses(chunk, response_id, state)
            for event in events:
                if event.get("type") == "response.completed":
                    # 补全 completed 的 response 对象 (zod 校验 usage 必填)
                    event["response"]["created_at"] = created_at
                    event["response"]["model"] = model
                    event["response"]["object"] = "response"
                    completed = True
                yield f"data: {json.dumps(event)}\n\n".encode()

        # 上游异常中断 (无 finish_reason): 补收尾事件, 避免客户端挂起
        if not completed:
            tail = _convert_chat_chunk_to_responses(
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                response_id, state,
            )
            for event in tail:
                if event.get("type") == "response.completed":
                    event["response"]["created_at"] = created_at
                    event["response"]["model"] = model
                    event["response"]["object"] = "response"
                yield f"data: {json.dumps(event)}\n\n".encode()

    return StreamingResponse(responses_stream(), media_type="text/event-stream")
