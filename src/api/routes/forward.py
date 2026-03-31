"""OpenAI 兼容的转发 API 路由."""

import uuid
import time
import json
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

from src.api.schemas.forward import (
    ModelList,
    ModelInfo,
    ChatCompletionRequest,
    ChatMessage,
    UsageInfo,
)
from src.modules.forward import Forwarder, ForwarderConfig, UpstreamError, UpstreamTimeout
from src.modules.memory import SqliteMemoryStore, MemoryEntry, Visibility
from src.modules.extraction import extract_latest_user_message
from src.modules.context import merge_context, deduplicate_messages

# OpenAI 兼容的路由，使用 /v1 前缀
router = APIRouter(prefix="/v1")

# 配置 (应从环境变量或配置文件加载)
UPSTREAM_CONFIG = ForwarderConfig(
    base_url="https://api.openai.com/v1",
    api_key="sk-placeholder",  # TODO: 从环境变量加载
    default_model="gpt-3.5-turbo",
)

# 记忆存储
MEMORY_STORE = SqliteMemoryStore("data/memories.db")


@router.get("/models", response_model=ModelList, tags=["Models"])
async def list_models():
    """列出可用模型."""
    return ModelList(
        object="list",
        data=[
            ModelInfo(
                id="mnemosync-any",
                object="model",
                created=1686935002,
                owned_by="mnemosync",
            )
        ],
    )


@router.get("/models/{model_id}", response_model=ModelInfo, tags=["Models"])
async def get_model(model_id: str):
    """获取特定模型信息."""
    if model_id == "mnemosync-any":
        return ModelInfo(
            id="mnemosync-any",
            object="model",
            created=1686935002,
            owned_by="mnemosync",
        )
    raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")


@router.post("/chat/completions", tags=["Chat Completions"])
async def create_chat_completion(request: ChatCompletionRequest, http_request: Request):
    """创建聊天补全.

    处理流程:
    1. 验证 API Key (中间件已完成)
    2. 加载历史记忆
    3. 合并上下文 (历史记忆 + 当前消息)
    4. 转发给上游模型
    5. 存储对话记录 (异步)
    6. 返回响应
    """
    # 初始化记忆存储
    await MEMORY_STORE.init_db()

    # 转换为 dict 格式
    messages_dict = [msg.model_dump() for msg in request.messages]

    # 去重
    messages_dict = deduplicate_messages(messages_dict)

    # 加载历史记忆 (所有记忆，当前版本不区分用户)
    # TODO: 根据 api_key_id 或 user_identifier 过滤
    memories = await MEMORY_STORE.query(
        source_user=None,  # 加载所有记忆
        limit=20,          # 最多 20 条历史
    )
    memories_dict = [
        {"role": mem.role, "content": mem.content}
        for mem in memories
    ]

    # 合并上下文
    merged_messages = merge_context(
        memories=memories_dict,
        messages=messages_dict,
    )

    # 创建转发器
    async with Forwarder(UPSTREAM_CONFIG) as forwarder:
        try:
            if request.stream:
                # 流式响应
                return await _handle_stream(
                    forwarder=forwarder,
                    messages=merged_messages,  # 使用合并后的上下文
                    request=request,
                    http_request=http_request,
                )
            else:
                # 非流式响应
                return await _handle_non_stream(
                    forwarder=forwarder,
                    messages=merged_messages,  # 使用合并后的上下文
                    request=request,
                    http_request=http_request,
                )

        except UpstreamTimeout as e:
            raise HTTPException(status_code=504, detail=str(e)) from e
        except UpstreamError as e:
            raise HTTPException(status_code=502, detail=f"Upstream error: {e.message}") from e


async def _handle_non_stream(
    forwarder: Forwarder,
    messages: list[dict[str, Any]],
    request: ChatCompletionRequest,
    http_request: Request,
) -> JSONResponse:
    """处理非流式请求."""
    # 发送到上游
    response = await forwarder.send(
        messages=messages,
        model=request.model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )

    # 异步存储对话 (不阻塞响应)
    asyncio.create_task(
        _store_conversation(
            messages=messages,
            response=response,
            api_key_id=http_request.state.api_key_id if hasattr(http_request.state, "api_key_id") else None,
        )
    )

    # 返回响应
    return JSONResponse(content=response)


async def _handle_stream(
    forwarder: Forwarder,
    messages: list[dict[str, Any]],
    request: ChatCompletionRequest,
    http_request: Request,
) -> StreamingResponse:
    """处理流式请求."""
    # 收集完整响应以便存储
    collected_chunks = []

    async def stream_generator():
        async for chunk in forwarder.send_stream(
            messages=messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        ):
            collected_chunks.append(chunk)
            yield chunk

        # 异步存储对话
        asyncio.create_task(
            _store_streamed_conversation(
                messages=messages,
                chunks=collected_chunks,
                api_key_id=http_request.state.api_key_id if hasattr(http_request.state, "api_key_id") else None,
            )
        )

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
    )


async def _store_conversation(
    messages: list[dict[str, Any]],
    response: dict[str, Any],
    api_key_id: str | None = None,
) -> None:
    """存储对话记录.

    Args:
        messages: 请求消息列表
        response: 上游响应
        api_key_id: API Key ID (用于标识前端来源)

    Note:
        当前版本 source_user 使用固定值，所有对话视为同一对话方。
        未来扩展时可根据 api_key_id 或 request.user 字段区分对话方。
    """
    try:
        # 当前版本：所有记忆属于同一对话方
        # TODO: 未来根据 request.user 或 api_key_id 映射到对话方标识
        source_user = "default"

        # 提取最新的一条用户消息
        latest_user_msg = extract_latest_user_message(messages)

        # 存储最新用户消息
        if latest_user_msg:
            entry = MemoryEntry.create(
                content=latest_user_msg.get("content", ""),
                role="user",
                source_user=source_user,
                visibility=Visibility.SOURCE_RESTRICTED,
            )
            await MEMORY_STORE.save(entry)

        # 存储助手回复 (属于同一对话)
        if response.get("choices"):
            assistant_content = response["choices"][0]["message"].get("content", "")
            entry = MemoryEntry.create(
                content=assistant_content,
                role="assistant",
                source_user=source_user,  # 与用户消息相同的 source_user
                visibility=Visibility.SOURCE_RESTRICTED,
            )
            await MEMORY_STORE.save(entry)

    except Exception as e:
        # 存储失败不影响响应，仅记录日志
        print(f"Failed to store conversation: {e}")


async def _store_streamed_conversation(
    messages: list[dict[str, Any]],
    chunks: list[bytes],
    api_key_id: str | None = None,
) -> None:
    """存储流式对话记录."""
    try:
        # 解析流式响应，提取完整内容
        assistant_content = _parse_stream_chunks(chunks)

        # 当前版本：所有记忆属于同一对话方
        source_user = "default"

        # 提取最新的一条用户消息
        latest_user_msg = extract_latest_user_message(messages)

        # 存储最新用户消息
        if latest_user_msg:
            entry = MemoryEntry.create(
                content=latest_user_msg.get("content", ""),
                role="user",
                source_user=source_user,
                visibility=Visibility.SOURCE_RESTRICTED,
            )
            await MEMORY_STORE.save(entry)

        # 存储助手回复 (属于同一对话)
        if assistant_content:
            entry = MemoryEntry.create(
                content=assistant_content,
                role="assistant",
                source_user=source_user,  # 与用户消息相同的 source_user
                visibility=Visibility.SOURCE_RESTRICTED,
            )
            await MEMORY_STORE.save(entry)

    except Exception as e:
        print(f"Failed to store streamed conversation: {e}")


def _parse_stream_chunks(chunks: list[bytes]) -> str:
    """解析流式响应分块，提取完整内容."""
    content_parts = []

    for chunk in chunks:
        try:
            # 跳过 [DONE] 标记
            if chunk.strip() == b"data: [DONE]":
                continue

            # 解析 SSE 格式
            if chunk.startswith(b"data: "):
                data = chunk[6:]  # 移除 "data: " 前缀
                parsed = json.loads(data)

                # 提取内容
                if parsed.get("choices"):
                    delta = parsed["choices"][0].get("delta", {})
                    if delta.get("content"):
                        content_parts.append(delta["content"])

        except (json.JSONDecodeError, KeyError, IndexError):
            # 跳过无效分块
            continue

    return "".join(content_parts)
