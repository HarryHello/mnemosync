"""幂等缓存: 查询/写入/重放."""
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.schemas.forward import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatMessage,
    UsageInfo,
)
from src.persistence.idempotency_store import IdempotencyRecord

from ._accessors import _get_idempotency_store

logger = logging.getLogger(__name__)


async def _lookup_idempotency(
    http_request: Request,
    api_key_id: str | None,
    external_event_id: str | None,
) -> IdempotencyRecord | None:
    """查询幂等缓存. 未命中/未配置/查询失败都返回 None (退化为正常流程).

    群聊平台重发同一条消息时, 命中首次响应并原样重放 — 避免重复的
    上游 LLM 调用与重复记忆写入。
    """
    if not external_event_id:
        return None
    store = _get_idempotency_store(http_request)
    if store is None:
        return None
    integration_id = api_key_id or "anonymous"
    try:
        return await store.get(integration_id, external_event_id)
    except Exception as e:
        logger.warning("幂等查询失败, 退化为正常流程: %s", e)
        return None


async def _record_idempotency(
    http_request: Request,
    api_key_id: str | None,
    external_event_id: str | None,
    event_id: str,
    response_text: str,
    response_message: dict[str, Any] | None = None,
    finish_reason: str | None = None,
) -> None:
    """写入幂等缓存 (首次成功响应). 失败仅告警, 不影响响应."""
    if not external_event_id or not response_text:
        return
    store = _get_idempotency_store(http_request)
    if store is None:
        return
    integration_id = api_key_id or "anonymous"
    try:
        response_message_json = (
            json.dumps(response_message, ensure_ascii=False) if response_message else None
        )
        await store.record(
            integration_id, external_event_id, event_id, response_text,
            response_message=response_message_json,
            finish_reason=finish_reason,
        )
    except Exception as e:
        logger.warning("幂等记录写入失败 (不影响响应): %s", e)


def _replay_json_response(record: IdempotencyRecord, model: str) -> JSONResponse:
    """非流式幂等重放: 原样返回首次响应 (同一 id, usage 归零)."""
    if record.response_message:
        try:
            message_payload = json.loads(record.response_message)
            message_payload.setdefault("role", "assistant")
            choice_message = ChatMessage.model_validate(message_payload)
        except (json.JSONDecodeError, Exception):
            choice_message = ChatMessage(
                role="assistant",
                content=record.response_text or "",
            )
    else:
        choice_message = ChatMessage(
            role="assistant",
            content=record.response_text or "",
        )
    return JSONResponse(
        content=ChatCompletionResponse(
            id=record.event_id,
            model=model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=choice_message,
                    finish_reason=record.finish_reason or "stop",
                )
            ],
            usage=UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        ).model_dump(exclude_none=True)
    )


def _replay_stream_response(record: IdempotencyRecord, model: str) -> StreamingResponse:
    """流式幂等重放: 把缓存响应拼成标准 SSE 序列 (内容帧 + finish 帧 + [DONE])."""

    async def replay_generator() -> AsyncGenerator[bytes, None]:
        created = int(datetime.now(UTC).timestamp())
        finish_reason = record.finish_reason or "stop"
        # 工具调用响应: 以 tool_calls 帧形式重放
        if record.response_message:
            try:
                message = json.loads(record.response_message)
                tool_calls = message.get("tool_calls")
                if tool_calls:
                    tool_chunk = {
                        "id": record.event_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": message.get("content"),
                                    "tool_calls": tool_calls,
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n".encode()
                    stop_chunk = {
                        "id": record.event_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": finish_reason,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return
            except (json.JSONDecodeError, Exception):
                pass
        # 普通文本响应
        content_chunk = {
            "id": record.event_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": record.response_text or ""},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n".encode()
        stop_chunk = {
            "id": record.event_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
        yield f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(replay_generator(), media_type="text/event-stream")
