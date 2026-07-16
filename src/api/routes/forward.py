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
from src.api.reasoning_control import (
    build_reasoning_stream_frames,
    chunk_has_native_reasoning,
    mark_native_reasoning,
    should_use_proxy_thinking,
)
from src.core.config import get_settings
from src.core.graph import build_graph
from src.core.memory.context import build_main_dialogue_messages
from src.core.agents import run_proxy_thinking, run_prompt_cleaning
from src.core.memory import format_relationship
from src.core.models.resolver import NoCandidateForRoleError
from src.infra.forwarder import (
    UpstreamError,
    UpstreamTimeout,
    parse_sse_stream,
)
from src.infra.forwarder.multi import (
    MultiForwarder,
    UpstreamAllCandidatesFailed,
)
from src.infra.llm_service.models import ModelType
from src.infra.vector_store import VectorStore
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.memory_store import SqliteMemoryStore
from src.tools import MemoryRetriever, make_sentence_classifier_tool

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


def _get_multi_forwarder(http_request: Request) -> MultiForwarder:
    """从 app.state 取共享 MultiForwarder (由 lifespan 建立)."""
    return http_request.app.state.multi_forwarder


async def _resolve_main_model(http_request: Request) -> str:
    """解析 MAIN 角色最高优先级候选的模型名 (供 usage/response.model/推理判定使用)."""
    resolver = http_request.app.state.resolver
    try:
        top = await resolver.first(ModelType.MAIN)
        return top.model
    except NoCandidateForRoleError:
        return "mnemosync-any"


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

    # 服务器优先人格: 从配置加载, 不从客户端 system 消息提取
    settings = get_settings()
    persona = settings.persona.prompt
    persona_name = settings.persona.name
    logger.debug("  服务器人格: %s (长度: %d)", persona_name, len(persona))

    # 提取客户端 system 消息 + 提示词清洗
    prompt_cleaning_result: dict[str, Any] | None = None
    client_system_msg = ""
    for msg in request.messages:
        if msg.role == "system" and msg.content:
            client_system_msg = msg.content
            break

    if client_system_msg.strip():
        logger.debug("  🧹 清洗客户端 system 消息 (长度: %d)", len(client_system_msg))
        multi_forwarder = _get_multi_forwarder(http_request)
        try:
            cleaning_tools = [make_sentence_classifier_tool(multi_forwarder)]
            cleaning_out = await run_prompt_cleaning(
                forwarder=multi_forwarder,
                system_message=client_system_msg,
                tools=cleaning_tools,
                max_iterations=3,
            )
            prompt_cleaning_result = {
                "retained": cleaning_out.retained,
                "discarded": cleaning_out.discarded,
                "reasoning": cleaning_out.reasoning,
            }
            if cleaning_out.retained:
                retained_text = "\n".join(cleaning_out.retained)
                persona = persona + "\n\n" + retained_text
                logger.debug("  ✅ 清洗完成: 保留 %d 条指令, 丢弃 %d 条人格描述",
                             len(cleaning_out.retained), len(cleaning_out.discarded))
            else:
                logger.debug("  ✅ 清洗完成: 无保留指令, 全部丢弃")
        except Exception as e:
            logger.warning("提示词清洗失败, 降级: 全部丢弃客户端 system 消息 (%s)", e)
            prompt_cleaning_result = {"retained": [], "discarded": [client_system_msg], "reasoning": str(e)}

    main_model = await _resolve_main_model(http_request)
    use_proxy = should_use_proxy_thinking(request, settings, main_model=main_model)
    logger.debug("  代理推理: %s (main_model=%s)", "启用" if use_proxy else "跳过", main_model)

    initial_state = {
        "messages": messages_dict,
        "source_user": source_user,
        "persona": persona,
        "persona_name": persona_name,
        "proxy_thinking_enabled": use_proxy,
        "stream_mode": bool(request.stream),
        "main_model": main_model,
    }
    if prompt_cleaning_result:
        initial_state["prompt_cleaning_result"] = prompt_cleaning_result

    if request.stream:
        return await _handle_stream(http_request, initial_state, request, use_proxy)
    else:
        return await _handle_non_stream(http_request, initial_state, request)


async def _handle_non_stream(
    http_request: Request,
    initial_state: dict[str, Any],
    request: ChatCompletionRequest,
) -> JSONResponse:
    """非流式: 运行完整图, 返回结果."""
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
    reasoning = final_state.get("proxy_thinking_result") or None
    upstream_usage = final_state.get("upstream_usage") or {}
    response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    main_model = initial_state.get("main_model") or await _resolve_main_model(http_request)

    usage_info = UsageInfo(
        prompt_tokens=int(upstream_usage.get("prompt_tokens", 0)),
        completion_tokens=int(upstream_usage.get("completion_tokens", 0)),
        total_tokens=int(upstream_usage.get("total_tokens", 0)),
    )

    logger.debug("📤 返回响应: %s (reasoning: %s, usage: %s)",
                 response_id, "有" if reasoning else "无", usage_info.model_dump())
    return JSONResponse(
        content=ChatCompletionResponse(
            id=response_id,
            model=main_model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=response_text,
                        reasoning_content=reasoning,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=usage_info,
        ).model_dump(exclude_none=True)
    )


async def _handle_stream(
    http_request: Request,
    initial_state: dict[str, Any],
    request: ChatCompletionRequest,
    use_proxy_thinking: bool,
) -> StreamingResponse:
    """流式: 加载记忆 → (可选) 代理推理 → 合成 reasoning_content SSE
    → 转发上游 → 后台记忆图.

    代理推理结果 (a) 作为 system prompt 注入主对话, (b) 拆帧作为
    delta.reasoning_content 提前吐给客户端, 与上游正文流拼接成完整回复.
    """
    settings = get_settings()
    source_user = initial_state.get("source_user", "default")
    main_model = initial_state.get("main_model") or await _resolve_main_model(http_request)
    multi_forwarder = _get_multi_forwarder(http_request)

    logger.debug("🧠 加载记忆上下文...")
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))

    perms = await memory_store.list_permanent(
        source_user, limit=settings.memory.permanent_load_top
    )
    logger.debug("  📚 永久记忆: %d 条", len(perms))

    conversation_history = initial_state.get("messages", [])
    query = ""
    for m in reversed(conversation_history):
        if m.get("role") == "user":
            query = m.get("content", "")
            break

    retrieved_entries: list = []
    if query:
        retriever = MemoryRetriever(multi_forwarder, vector_store, memory_store)
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

    rel = await memory_store.get_relationship("default", source_user)
    logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")

    # 4. 代理推理 (可选, 同步, 与检索串行)
    reasoning_text: str | None = None
    if use_proxy_thinking:
        logger.debug("🤔 [代理推理] 开始 (ASSIST role)")
        try:
            perms_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"
            user_msg_for_thinking = query
            reasoning_text = await run_proxy_thinking(
                forwarder=multi_forwarder,
                user_name=source_user,
                relationship=format_relationship(rel) if rel else "新用户",
                memories=perms_text,
                user_message=user_msg_for_thinking,
                tools=None,
            )
            logger.debug("  ✅ 代理推理完成, 长度: %d", len(reasoning_text) if reasoning_text else 0)
        except Exception as e:
            logger.warning("代理推理失败, 退化为普通转发: %s", e)
            reasoning_text = None

    # 5. 构建带记忆 + 推理注入的 prompt
    persona = initial_state.get("persona") or settings.persona.prompt
    persona_name = initial_state.get("persona_name") or settings.persona.name
    messages_dict = initial_state["messages"]

    conversation_history = [m for m in messages_dict if m.get("role") != "system"]

    messages_with_memory = build_main_dialogue_messages(
        persona_prompt=persona,
        persona_name=persona_name,
        user_name=source_user,
        permanent_memories=perms,
        retrieved_memories=retrieved_entries,
        relationship=rel,
        conversation_history=conversation_history,
        proxy_thinking_result=reasoning_text,
    )

    logger.debug("  📝 构建消息数: %d (含记忆上下文)", len(messages_with_memory))

    chatcmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    passthrough: dict[str, Any] = {}
    _optional_fields = (
        "tools", "tool_choice", "response_format", "stream_options",
        "top_p", "stop", "seed", "frequency_penalty", "presence_penalty",
        "logit_bias", "logprobs", "top_logprobs", "n", "user",
        "reasoning_effort", "reasoning", "thinking",
    )
    for _f in _optional_fields:
        _v = getattr(request, _f, None)
        if _v is not None:
            passthrough[_f] = _v
    if passthrough:
        logger.debug("  🔗 透传上游可选字段: %s", list(passthrough.keys()))

    async def stream_generator():
        if reasoning_text:
            for frame in build_reasoning_stream_frames(
                reasoning_text, chatcmpl_id=chatcmpl_id, model=main_model,
            ):
                yield frame

        collected_chunks: list[bytes] = []
        saw_native = False
        try:
            logger.debug("🚀 开始流式转发 (带记忆上下文)...")
            async for chunk in multi_forwarder.chat_stream(
                ModelType.MAIN,
                messages=messages_with_memory,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **passthrough,
            ):
                collected_chunks.append(chunk)
                if not saw_native and chunk_has_native_reasoning(chunk):
                    saw_native = True
                yield chunk
            logger.debug("✅ 流式转发完成, chunks: %d", len(collected_chunks))
        except UpstreamTimeout as e:
            logger.debug("⏰ 流式超时: %s", e)
            yield f'data: {{"error": "{e}"}}\n\n'.encode("utf-8")
            return
        except UpstreamError as e:
            logger.debug("❌ 流式错误: %s", e.message)
            yield f'data: {{"error": "{e.message}"}}\n\n'.encode("utf-8")
            return
        except UpstreamAllCandidatesFailed as e:
            logger.debug("❌ 所有候选失败: %s", e)
            yield f'data: {{"error": "all candidates failed: {e}"}}\n\n'.encode("utf-8")
            return
        except NoCandidateForRoleError as e:
            logger.debug("❌ 无候选: %s", e)
            yield f'data: {{"error": "no candidate: {e}"}}\n\n'.encode("utf-8")
            return

        if saw_native:
            mark_native_reasoning(main_model)

        logger.debug("🔄 触发后台记忆图...")
        initial_state["proxy_thinking_enabled"] = False
        if reasoning_text:
            initial_state["proxy_thinking_result"] = reasoning_text
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
