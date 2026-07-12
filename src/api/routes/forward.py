"""OpenAI 兼容的转发 API 路由.

对外提供 /v1/chat/completions 和 /v1/models.
接收请求 → API Key 验证 → 构建初始 state → 编译图 ainvoke → 返回响应.
流式: 直接通过 Forwarder 转发给上游, 异步触发记忆图.
"""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.schemas.forward import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ModelInfo,
    ModelList,
    UsageInfo,
)
from src.core.config import get_settings
from src.core.graph import build_graph
from src.infra.forwarder import (
    Forwarder,
    ForwarderConfig,
    UpstreamError,
    UpstreamTimeout,
    parse_sse_stream,
)
from src.persistence.api_key_store import SqliteApiKeyStore

router = APIRouter(prefix="/v1")

# 全局缓存
_api_key_store: SqliteApiKeyStore | None = None
_compiled_graph = None


def _get_api_key_store() -> SqliteApiKeyStore:
    global _api_key_store
    if _api_key_store is None:
        _api_key_store = SqliteApiKeyStore("data/api_keys.db")
    return _api_key_store


def _get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def _verify_api_key(request: Request) -> str | None:
    """从 Authorization header 验证 API Key, 返回 api_key_id 或 None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw_key = auth[7:]
    store = _get_api_key_store()
    api_key = await store.get_by_raw_key(raw_key)
    if api_key is None:
        return None
    await store.update_last_used(api_key.id)
    return api_key.id


# ── Models ─────────────────────────────────────────────────────


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


# ── Chat Completions ───────────────────────────────────────────


@router.post("/chat/completions", tags=["Chat Completions"])
async def create_chat_completion(request: ChatCompletionRequest, http_request: Request):
    """创建聊天补全.

    处理流程:
    1. API Key 验证 (中间件可选)
    2. 构建初始 AgentState
    3. 非流式: 图 ainvoke → 返回 response
    4. 流式: 直接 Forwarder 转发, 异步触发记忆图
    """
    # 构建初始 state
    messages_dict = [msg.model_dump(exclude_none=True) for msg in request.messages]

    # 提取 source_user (从 user 字段或默认)
    source_user = request.user or "default"

    # 提取 persona (从 system 消息)
    persona = "你是一个温暖、有记忆能力的 AI 助手。"
    persona_name = "助手"
    for msg in request.messages:
        if msg.role == "system" and msg.content:
            persona = msg.content
            break

    initial_state = {
        "messages": messages_dict,
        "source_user": source_user,
        "persona": persona,
        "persona_name": persona_name,
        "proxy_thinking_enabled": False,
        "stream_mode": bool(request.stream),
    }

    if request.stream:
        return await _handle_stream(http_request, initial_state, request)
    else:
        return await _handle_non_stream(initial_state, request)


async def _handle_non_stream(
    initial_state: dict[str, Any],
    request: ChatCompletionRequest,
) -> JSONResponse:
    """非流式: 运行完整图, 返回结果."""
    settings = get_settings()
    graph = _get_compiled_graph()

    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph execution failed: {e}") from e

    response_text = final_state.get("response", "")
    response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    return JSONResponse(
        content=ChatCompletionResponse(
            id=response_id,
            model=settings.chat.main_model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=response_text),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        ).model_dump()
    )


async def _handle_stream(
    http_request: Request,
    initial_state: dict[str, Any],
    request: ChatCompletionRequest,
) -> StreamingResponse:
    """流式: 直接转发给上游, 异步触发记忆图.

    流式模式下不等待图完成 (记忆写入在后台).
    """
    settings = get_settings()

    # 构建上游请求
    messages_dict = initial_state["messages"]

    forwarder_config = ForwarderConfig(
        base_url=settings.chat.base_url,
        api_key=settings.chat.api_key,
        default_model=settings.chat.main_model,
        timeout=90.0,
    )

    async def stream_generator():
        collected_chunks: list[bytes] = []
        async with Forwarder(forwarder_config) as forwarder:
            try:
                async for chunk in forwarder.chat_stream(
                    messages=messages_dict,
                    model=request.model or settings.chat.main_model,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                ):
                    collected_chunks.append(chunk)
                    yield chunk
            except UpstreamTimeout as e:
                yield f'data: {{"error": "{e}"}}\n\n'
                return
            except UpstreamError as e:
                yield f'data: {{"error": "{e.message}"}}\n\n'
                return

        # 流结束后, 异步触发记忆图 (不阻塞)
        asyncio.create_task(_run_memory_graph(initial_state, collected_chunks))

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
    )


async def _run_memory_graph(
    initial_state: dict[str, Any],
    stream_chunks: list[bytes],
) -> None:
    """异步执行记忆图 (流式模式下后台运行)."""
    # 补充 response 到 state (从流 chunks 中拼接)
    response_text = parse_sse_stream(stream_chunks)
    initial_state["response"] = response_text
    initial_state["response_chunks"] = stream_chunks

    graph = _get_compiled_graph()
    try:
        await graph.ainvoke(initial_state)
    except Exception as e:
        # 后台任务失败仅日志
        import logging
        logging.getLogger(__name__).warning("后台记忆图执行失败: %s", e)
