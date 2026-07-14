"""OpenAI 兼容的转发 API 路由.

对外提供 /v1/chat/completions 和 /v1/models.
接收请求 → API Key 验证 → 构建初始 state → 编译图 ainvoke → 返回响应.
流式: 加载记忆 → 构建上下文 → 转发给上游 → 异步触发记忆图.
"""

import asyncio
import logging
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
from src.core.memory.context import build_main_dialogue_messages
from src.infra.forwarder import (
    Forwarder,
    ForwarderConfig,
    UpstreamError,
    UpstreamTimeout,
    parse_sse_stream,
)
from src.infra.vector_store import VectorStore
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.memory_store import SqliteMemoryStore
from src.tools import MemoryRetriever
from src.core.memory import format_relationship

router = APIRouter(prefix="/v1")
logger = logging.getLogger(__name__)

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
    logger.debug("=" * 60)
    logger.debug("📥 收到 chat/completions 请求")
    logger.debug("  model: %s", request.model)
    logger.debug("  stream: %s", request.stream)
    logger.debug("  temperature: %s", request.temperature)
    logger.debug("  max_tokens: %s", request.max_tokens)
    logger.debug("  messages count: %d", len(request.messages))

    # 验证模型名称: 只接受 mnemosync-any 或空（使用默认模型）
    if request.model and request.model != "mnemosync-any":
        logger.debug("  ❌ 无效模型: %s", request.model)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model '{request.model}'. Use 'mnemosync-any' or omit the field."
        )

    # 构建初始 state
    messages_dict = [msg.model_dump(exclude_none=True) for msg in request.messages]
    logger.debug("  构建 state 完成, 消息数: %d", len(messages_dict))

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

    logger.debug("🚀 开始执行图 (非流式)...")
    try:
        final_state = await graph.ainvoke(initial_state)
        logger.debug("✅ 图执行完成")
        logger.debug("  response 长度: %d", len(final_state.get("response", "")))
    except Exception as e:
        logger.debug("❌ 图执行失败: %s", e)
        raise HTTPException(status_code=500, detail=f"Graph execution failed: {e}") from e

    response_text = final_state.get("response", "")
    response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    logger.debug("📤 返回响应: %s", response_id)
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
    """流式: 加载记忆 → 构建上下文 → 转发给上游 → 异步触发记忆图.

    流式模式下先加载记忆再流式回复.
    """
    settings = get_settings()
    source_user = initial_state.get("source_user", "default")

    # 加载记忆
    logger.debug("🧠 加载记忆上下文...")
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))

    # 1. 加载永久记忆
    perms = await memory_store.list_permanent(
        source_user, limit=settings.memory.permanent_load_top
    )
    logger.debug("  📚 永久记忆: %d 条", len(perms))

    # 2. 语义检索相关记忆
    conversation_history = initial_state.get("messages", [])
    query = ""
    for m in reversed(conversation_history):
        if m.get("role") == "user":
            query = m.get("content", "")
            break

    retrieved_entries: list = []
    if query:
        forwarder_config = ForwarderConfig(
            base_url=settings.chat.base_url,
            api_key=settings.chat.api_key,
            default_model=settings.chat.main_model,
            timeout=30.0,
        )
        async with Forwarder(forwarder_config) as forwarder:
            retriever = MemoryRetriever(forwarder, vector_store, memory_store)
            results = await retriever.search(
                query, top_k=settings.memory.retrieval_top_k,
                source_user=source_user,
            )
            for r in results:
                await memory_store.mark_accessed(r.memory_id)
                entry = await memory_store.get_by_id(r.memory_id)
                if entry:
                    retrieved_entries.append(entry)
        logger.debug("  🔍 检索结果: %d 条", len(retrieved_entries))

    # 3. 加载关系状态
    rel = await memory_store.get_relationship("default", source_user)
    logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")

    # 4. 构建带记忆的 prompt
    persona = initial_state.get("persona", "你是一个温暖、有记忆能力的 AI 助手。")
    persona_name = initial_state.get("persona_name", "助手")
    messages_dict = initial_state["messages"]

    # 去掉原始 system 消息，用我们的拼装
    conversation_history = [m for m in messages_dict if m.get("role") != "system"]

    messages_with_memory = build_main_dialogue_messages(
        persona_prompt=persona,
        persona_name=persona_name,
        user_name=source_user,
        permanent_memories=perms,
        retrieved_memories=retrieved_entries,
        relationship=rel,
        conversation_history=conversation_history,
    )

    logger.debug("  📝 构建消息数: %d (含记忆上下文)", len(messages_with_memory))

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
                logger.debug("🚀 开始流式转发 (带记忆上下文)...")
                async for chunk in forwarder.chat_stream(
                    messages=messages_with_memory,
                    model=settings.chat.main_model,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                ):
                    collected_chunks.append(chunk)
                    yield chunk
                logger.debug("✅ 流式转发完成, chunks: %d", len(collected_chunks))
            except UpstreamTimeout as e:
                logger.debug("⏰ 流式超时: %s", e)
                yield f'data: {{"error": "{e}"}}\n\n'
                return
            except UpstreamError as e:
                logger.debug("❌ 流式错误: %s", e.message)
                yield f'data: {{"error": "{e.message}"}}\n\n'
                return

        # 流结束后, 异步触发记忆图 (不阻塞)
        logger.debug("🔄 触发后台记忆图...")
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
