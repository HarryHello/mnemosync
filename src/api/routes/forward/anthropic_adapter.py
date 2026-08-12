"""Anthropic Messages API 下游适配器.

提供 POST /v1/messages 端点, 接受 Anthropic Messages API 格式请求,
转换为内部 OpenAI 格式处理, 再转换回 Anthropic 格式响应.
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

# 无 prefix — 由父 router (forward/__init__.py, prefix="/v1") 拼接, 否则会变成 /v1/v1/messages
router = APIRouter()


# ── 请求/响应 Schema ──────────────────────────────────────────


class AnthropicContentBlock(BaseModel):
    type: str
    text: str | None = None
    source: dict[str, Any] | None = None
    tool_use_id: str | None = None
    content: str | list[dict[str, Any]] | None = None
    # tool_use block 字段
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    # thinking block
    thinking: str | None = None


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
            continue

        if not isinstance(msg.content, list):
            continue

        # Anthropic content blocks → OpenAI
        # 参考 cc-switch transform.rs convert_message_to_openai:
        #   text → content parts; tool_use → assistant.tool_calls;
        #   tool_result → 单独 tool 消息; thinking → 丢弃
        text_parts: list[str] = []
        openai_parts: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        has_tool_result = False

        for block in msg.content:
            if block.type == "text":
                text_parts.append(block.text or "")
                openai_parts.append({"type": "text", "text": block.text or ""})
            elif block.type == "image":
                source = block.source or {}
                if source.get("type") == "base64":
                    media_type = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    openai_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    })
                elif source.get("type") == "url":
                    openai_parts.append({
                        "type": "image_url",
                        "image_url": {"url": source.get("url", "")},
                    })
            elif block.type == "tool_use":
                # tool_use → OpenAI assistant.tool_calls (arguments 为 JSON 字符串)
                tool_calls.append({
                    "id": block.id or "",
                    "type": "function",
                    "function": {
                        "name": block.name or "",
                        "arguments": json.dumps(block.input or {}, ensure_ascii=False),
                    },
                })
            elif block.type == "tool_result":
                # tool_result → 单独 tool 消息
                has_tool_result = True
                messages.append({
                    "role": "tool",
                    "tool_call_id": block.tool_use_id or "",
                    "content": block.content if isinstance(block.content, str) else json.dumps(block.content, ensure_ascii=False),
                })
            elif block.type == "thinking":
                # 思考块丢弃 (内部管线不消费 Anthropic thinking)
                pass

        # assistant + tool_calls → OpenAI assistant message (content 可 null)
        if tool_calls:
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
            }
            assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
        elif openai_parts:
            messages.append({"role": msg.role, "content": openai_parts})
        elif not has_tool_result:
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


@dataclass
class _AnthropicStreamState:
    """跨 chunk 的 Anthropic 流式状态 (参考 cc-switch src-tauri/src/proxy/providers/streaming.rs).

    关键实践:
    - 块切换管理: 文本/thinking/工具 block 切换时先 stop 上一个, 再 start 新的
    - tool_use 延迟 start: id+name 都齐了才 start, start 前累积的 args 在 start 后补发
    - message_delta 去重: 上游可能发多个 finish_reason chunk, 只发一次, 否则
      Claude Code abort 连接
    """

    next_content_index: int = 0
    # 当前非工具块 (text / thinking)
    current_block_type: str | None = None
    current_block_index: int | None = None
    # openai tool index → 工具块状态
    tool_blocks: dict[int, dict[str, Any]] = field(default_factory=dict)
    open_tool_indices: set[int] = field(default_factory=set)
    # message_delta 去重 (只发一次)
    has_emitted_message_delta: bool = False


def _stop_current_block(state: _AnthropicStreamState, events: list[dict[str, Any]]) -> None:
    """stop 当前非工具块 (text/thinking)."""
    if state.current_block_index is not None:
        events.append({
            "type": "content_block_stop",
            "index": state.current_block_index,
        })
        state.current_block_type = None
        state.current_block_index = None


def _start_non_tool_block(
    block_type: str, state: _AnthropicStreamState, events: list[dict[str, Any]],
) -> None:
    """切换到新的非工具块 (text/thinking), 先 stop 旧的."""

    if state.current_block_type == block_type:
        return
    _stop_current_block(state, events)
    index = state.next_content_index
    state.next_content_index += 1
    if block_type == "text":
        block: dict[str, Any] = {"type": "text", "text": ""}
    else:
        block = {"type": "thinking", "thinking": ""}
    events.append({
        "type": "content_block_start",
        "index": index,
        "content_block": block,
    })
    state.current_block_type = block_type
    state.current_block_index = index


def _convert_openai_chunk_to_anthropic(
    chunk: dict[str, Any],
    state: _AnthropicStreamState | None = None,
) -> list[dict[str, Any]]:
    """将 OpenAI SSE chunk 转换为 Anthropic SSE 事件列表.

    状态化处理 (参考 cc-switch streaming.rs):
    - 块切换: text ↔ thinking ↔ tool 切换时先 stop 再 start
    - 工具名跨帧累积, id+name 齐才 start, args 缓冲后补发
    - message_delta 去重 (只发一次)
    """
    events: list[dict[str, Any]] = []
    if state is None:
        state = _AnthropicStreamState()
    choices = chunk.get("choices", [])
    if not choices:
        return events

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # 思考块 (OpenAI reasoning → Anthropic thinking)
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning:
        _start_non_tool_block("thinking", state, events)
        events.append({
            "type": "content_block_delta",
            "index": state.current_block_index,
            "delta": {"type": "thinking_delta", "thinking": reasoning},
        })

    # 文本 delta
    content = delta.get("content")
    if content:
        _start_non_tool_block("text", state, events)
        events.append({
            "type": "content_block_delta",
            "index": state.current_block_index,
            "delta": {"type": "text_delta", "text": content},
        })

    # 工具调用 (注意: 键存在但值为 null 时 .get 默认值不生效, 需 or [])
    tool_calls = delta.get("tool_calls") or []
    if tool_calls:
        # 工具调用出现 → stop 当前非工具块
        _stop_current_block(state, events)
        for tc in tool_calls:
            func = tc.get("function", {})
            oidx = tc.get("index", 0) if isinstance(tc.get("index"), int) else len(state.tool_blocks)
            if oidx not in state.tool_blocks:
                block: dict[str, Any] = {
                    "anthropic_index": state.next_content_index,
                    "id": "",
                    "name": "",
                    "started": False,
                    "pending_args": "",
                }
                state.next_content_index += 1
                state.tool_blocks[oidx] = block
            else:
                block = state.tool_blocks[oidx]
            if tc.get("id"):
                block["id"] = tc["id"]
            if func.get("name"):
                block["name"] += func["name"]

            args_delta = func.get("arguments") or ""
            if args_delta:
                # 到 arguments 时 start — 此时 name 已累积完整 (跨帧分片也正确)
                if not block["started"] and bool(block["id"]) and bool(block["name"]):
                    block["started"] = True
                    events.append({
                        "type": "content_block_start",
                        "index": block["anthropic_index"],
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    })
                    state.open_tool_indices.add(block["anthropic_index"])
                    # start 前缓冲的 args 补发
                    pending = block.get("pending_args", "")
                    if pending:
                        events.append({
                            "type": "content_block_delta",
                            "index": block["anthropic_index"],
                            "delta": {"type": "input_json_delta", "partial_json": pending},
                        })
                        block["pending_args"] = ""
                if block["started"]:
                    events.append({
                        "type": "content_block_delta",
                        "index": block["anthropic_index"],
                        "delta": {"type": "input_json_delta", "partial_json": args_delta},
                    })
                else:
                    # 未 start (id/name 缺失): 缓冲, start 后补发
                    block["pending_args"] += args_delta

    # finish_reason → 收尾 (去重 message_delta)
    if finish_reason and not state.has_emitted_message_delta:
        state.has_emitted_message_delta = True
        _stop_current_block(state, events)

        # late start: 未 start 但有 payload 的工具 (id/name 缺失时 fallback)
        for oidx in sorted(state.tool_blocks):
            block = state.tool_blocks[oidx]
            if block["started"]:
                continue
            has_payload = bool(block.get("pending_args")) or bool(block["id"]) or bool(block["name"])
            if not has_payload:
                continue
            block["started"] = True
            block["id"] = block["id"] or f"tool_call_{oidx}"
            block["name"] = block["name"] or "unknown_tool"
            events.append({
                "type": "content_block_start",
                "index": block["anthropic_index"],
                "content_block": {
                    "type": "tool_use",
                    "id": block["id"],
                    "name": block["name"],
                    "input": {},
                },
            })
            state.open_tool_indices.add(block["anthropic_index"])
            pending = block.get("pending_args", "")
            if pending:
                events.append({
                    "type": "content_block_delta",
                    "index": block["anthropic_index"],
                    "delta": {"type": "input_json_delta", "partial_json": pending},
                })

        # stop 所有 open 的工具块 (排序)
        for index in sorted(state.open_tool_indices):
            events.append({"type": "content_block_stop", "index": index})
        state.open_tool_indices.clear()

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


@router.post("/messages/count_tokens", tags=["Anthropic"])
async def count_tokens(body: AnthropicMessagesRequest) -> dict[str, Any]:
    """估算输入 token 数 (Anthropic Messages API 兼容).

    Cherry Studio / Claude Code 客户端在发送前调用该端点预估 token 用量.
    返回结构: {"input_tokens": N}. 使用与短期记忆一致的启发式估算
    (len//2 + 8), 不保证与上游 tokenizer 完全一致 (仅用于预估).
    """
    total = 0

    def _est(text: str) -> int:
        return len(text) // 2 + 8

    if body.system:
        system_text = body.system if isinstance(body.system, str) else json.dumps(body.system, ensure_ascii=False)
        total += _est(system_text)
    for msg in body.messages:
        if isinstance(msg.content, str):
            total += _est(msg.content)
        elif isinstance(msg.content, list):
            for block in msg.content:
                if block.text:
                    total += _est(block.text)
    return {"input_tokens": total}


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
        state = _AnthropicStreamState()

        # message_start 事件
        yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': message_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model, 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n".encode()

        # 转发流式数据 (文本 block 懒创建, 工具 block 状态化累积)
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
            anthropic_events = _convert_openai_chunk_to_anthropic(chunk, state)
            for event in anthropic_events:
                event_type = event.get("type", "")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n".encode()

        # 上游未给 finish_reason (异常中断): 触发收尾 (late start + stop 块 + message_delta)
        if not state.has_emitted_message_delta:
            tail = _convert_openai_chunk_to_anthropic(
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                state,
            )
            for event in tail:
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

        # message_stop
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode()

    return StreamingResponse(anthropic_stream(), media_type="text/event-stream")
