"""OpenAI 兼容的转发 API 路由.

对外提供 /v1/chat/completions 和 /v1/models.
接收请求 → API Key 验证 → 构建初始 state → 编译图 ainvoke → 返回响应.
流式: 加载记忆 → 构建上下文 → 转发给上游 → 异步触发记忆图.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
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
from src.core.memory import format_relationship
from src.core.memory.context import (
    build_main_dialogue_messages,
    render_main_dialogue_system,
)
from src.core.memory.short_term import (
    build_short_term_history,
    token_count_for_storage,
)
from src.core.agents import run_proxy_thinking, run_prompt_cleaning
from src.core.models.resolver import NoCandidateForRoleError
from src.infra.debug_context import use_agent
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
from src.persistence.api_key_store import ApiKey, SqliteApiKeyStore
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.identity_store import SqliteIdentityStore
from src.persistence.idempotency_store import IdempotencyRecord, SqliteIdempotencyStore
from src.core.identity import IdentityResolver, IdentityContext
from src.persistence.memory_store import SqliteMemoryStore
from src.tools import MemoryRetriever

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


def _get_conversation_store(http_request: Request) -> SqliteConversationStore:
    """从 app.state 取共享 SqliteConversationStore (由 lifespan 建立)."""
    return http_request.app.state.conversation_store


def _get_identity_store(http_request: Request) -> SqliteIdentityStore | None:
    """从 app.state 取共享 SqliteIdentityStore (由 lifespan 建立)."""
    return getattr(http_request.app.state, "identity_store", None)


def _get_idempotency_store(http_request: Request) -> SqliteIdempotencyStore | None:
    """从 app.state 取共享 SqliteIdempotencyStore (由 lifespan 建立)."""
    return getattr(http_request.app.state, "idempotency_store", None)


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
) -> None:
    """写入幂等缓存 (首次成功响应). 失败仅告警, 不影响响应."""
    if not external_event_id or not response_text:
        return
    store = _get_idempotency_store(http_request)
    if store is None:
        return
    integration_id = api_key_id or "anonymous"
    try:
        await store.record(integration_id, external_event_id, event_id, response_text)
    except Exception as e:
        logger.warning("幂等记录写入失败 (不影响响应): %s", e)


def _replay_json_response(record: IdempotencyRecord, model: str) -> JSONResponse:
    """非流式幂等重放: 原样返回首次响应 (同一 id, usage 归零)."""
    return JSONResponse(
        content=ChatCompletionResponse(
            id=record.event_id,
            model=model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=record.response_text or "",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        ).model_dump(exclude_none=True)
    )


def _replay_stream_response(record: IdempotencyRecord, model: str) -> StreamingResponse:
    """流式幂等重放: 把缓存文本拼成标准 SSE 序列 (单内容帧 + stop 帧 + [DONE])."""

    async def replay_generator():
        created = int(datetime.now(timezone.utc).timestamp())
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
        yield f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        stop_chunk = {
            "id": record.event_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    return StreamingResponse(replay_generator(), media_type="text/event-stream")


async def _resolve_main_candidate(http_request: Request):
    """解析 MAIN 角色首选候选. 返回 ResolvedCandidate 或 None (无候选)."""
    resolver = http_request.app.state.resolver
    try:
        return await resolver.first(ModelType.MAIN)
    except NoCandidateForRoleError:
        return None


async def _resolve_main_model(http_request: Request) -> str:
    """解析 MAIN 角色首选候选的模型名 (供 usage/response.model/推理判定使用)."""
    cand = await _resolve_main_candidate(http_request)
    return cand.model if cand else "mnemosync-any"


async def _resolve_source_frontend(request: Request, api_key_id: str | None) -> str | None:
    """从 API Key note 派生 source_frontend 元数据.

    v0.2.6: 用于回写 conversation_turns.source_frontend, 仅调试/追溯用,
    不作为查询条件. 服务器 side 派生, 不依赖客户端。
    """
    if api_key_id is None:
        return None
    store = getattr(request.app.state, "api_key_store", None) or _get_api_key_store()
    try:
        ak: ApiKey | None = await store.get_by_id(api_key_id)
    except Exception:
        return None
    return ak.note if ak else None


async def _resolve_identity_context(
    http_request: Request,
    api_key: ApiKey | None,
    request_user: str | None,
    messages: list,
) -> IdentityContext | None:
    """解析请求中的身份信息。

    1. 获取 API Key 绑定的策略
    2. 解析身份
    3. 返回 IdentityContext（None = 非归属模式）
    """
    identity_store = _get_identity_store(http_request)
    if identity_store is None:
        return None

    strategy_id = api_key.strategy_id if api_key else None
    if not strategy_id:
        return None

    strategy = await identity_store.get_strategy(strategy_id)
    if strategy is None or not strategy.is_active:
        return None

    config = json.loads(strategy.config) if strategy.config else {}
    forwarder = _get_multi_forwarder(http_request)
    resolver = IdentityResolver(identity_store, forwarder)
    return await resolver.resolve(
        request_user=request_user,
        messages=messages,
        strategy_type=strategy.strategy_type,
        strategy_config=config,
        strategy_name=strategy.name,
    )


async def _verify_api_key(request: Request) -> ApiKey | None:
    """从 Authorization header 验证 API Key, 返回 ApiKey 对象 (含 strategy_id) 或 None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw_key = auth[7:]
    store = _get_api_key_store()
    api_key = await store.get_by_raw_key(raw_key)
    if api_key is None:
        return None
    await store.update_last_used(api_key.id)
    return api_key


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

    # 服务器侧派生 source_frontend (来自 API Key 的 note, 仅元数据).
    # 客户端不需要传任何 header, 不需要修改客户端 (见 no-client-modifications)
    api_key = await _verify_api_key(http_request)
    api_key_id = api_key.id if api_key else None
    source_frontend = await _resolve_source_frontend(http_request, api_key_id)
    if source_frontend:
        logger.debug("  🔖 source_frontend: %s", source_frontend)

    # 构建初始 state
    messages_dict = [msg.model_dump(exclude_none=True) for msg in request.messages]
    logger.debug("  构建 state 完成, 消息数: %d", len(messages_dict))

    # 身份解析：通过 API Key 绑定的策略识别参与者
    identity_ctx = await _resolve_identity_context(
        http_request, api_key, request.user, messages_dict,
    )
    actor_id = identity_ctx.actor_id if identity_ctx else None
    source_user = identity_ctx.effective_user_id if identity_ctx else (request.user or None)
    space_id = identity_ctx.space_id if identity_ctx else None
    channel_type = identity_ctx.channel_type if identity_ctx else None
    external_event_id = identity_ctx.external_event_id if identity_ctx else None
    if not external_event_id:
        # 可选兜底: 客户端主动带 Idempotency-Key 头时也接受 (不要求客户端适配)
        external_event_id = http_request.headers.get("Idempotency-Key") or None

    # 幂等: 平台重发同一事件 → 直接重放首次响应 (在提示词清洗/上游调用之前,
    # 重复请求不产生任何 LLM 开销与记忆副作用)
    if external_event_id:
        replay = await _lookup_idempotency(http_request, api_key_id, external_event_id)
        if replay is not None:
            logger.info("🔁 幂等命中 (%s), 重放缓存响应", external_event_id)
            replay_model = await _resolve_main_model(http_request)
            if request.stream:
                return _replay_stream_response(replay, replay_model)
            return _replay_json_response(replay, replay_model)

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
            cleaning_out = await run_prompt_cleaning(
                forwarder=multi_forwarder,
                system_message=client_system_msg,
            )
            prompt_cleaning_result = {
                "clean_prompt": cleaning_out.clean_prompt,
                "reasoning": cleaning_out.reasoning,
            }
            if cleaning_out.clean_prompt:
                persona = persona + "\n\n" + cleaning_out.clean_prompt
                logger.debug("  ✅ 清洗完成: 输出长度 %d", len(cleaning_out.clean_prompt))
            else:
                logger.debug("  ✅ 清洗完成: 无保留指令, 全部丢弃")
        except Exception as e:
            logger.warning("提示词清洗失败, 降级: 全部丢弃客户端 system 消息 (%s)", e)
            prompt_cleaning_result = {"clean_prompt": "", "reasoning": str(e)}

    main_model = await _resolve_main_model(http_request)
    use_proxy = should_use_proxy_thinking(request, settings, main_model=main_model)
    logger.debug("  代理推理: %s (main_model=%s)", "启用" if use_proxy else "跳过", main_model)

    initial_state = {
        "messages": messages_dict,
        "source_user": source_user,
        "actor_id": actor_id,
        "persona": persona,
        "persona_name": persona_name,
        "persona_id": "default",
        "proxy_thinking_enabled": use_proxy,
        "stream_mode": bool(request.stream),
        "main_model": main_model,
        "source_frontend": source_frontend,
        "space_id": space_id,
        "channel_type": channel_type,
        "external_event_id": external_event_id,
        "api_key_id": api_key_id,
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
    """非流式: 运行完整图, 返回结果.

    与流式一致地做跨前端对话流水装填 (v0.2.6): 客户端 history 不再直接进
    state.messages, 而是替换为服务器侧流水裁剪结果 + 本轮新用户消息.
    图内节点 (`main_dialogue_node`) 会读装填后的 messages 拼 system。
    """
    settings = get_settings()
    conversation_store = _get_conversation_store(http_request)
    source_user = initial_state.get("source_user") or ""
    source_frontend = initial_state.get("source_frontend")
    actor_id = initial_state.get("actor_id")
    space_id = initial_state.get("space_id")
    external_event_id = initial_state.get("external_event_id")
    api_key_id = initial_state.get("api_key_id")

    main_candidate = await _resolve_main_candidate(http_request)
    main_ctx_length = main_candidate.context_length if main_candidate else None

    # 提取本轮新用户消息
    client_messages = initial_state.get("messages", [])
    new_user_content = ""
    for m in reversed(client_messages):
        if m.get("role") == "user":
            new_user_content = m.get("content", "")
            break

    # 简化: 非流式的 system 内容无法在装填前精确算 (需要 perms + retrieved),
    # 这些又是 graph 内节点做的. 保守估算: 用 persona + 一个"典型 system 长度"
    # 上限占位 (perms 15 条 * ~200 char + 检索 5 条 * ~200 char ~ 4k chars ~ 2k tok).
    # 主要目标是限制 history 总量在 ctx 内, 稍微保守可接受。
    settings_persona = initial_state.get("persona") or settings.persona.prompt
    system_estimate = settings_persona + "\n\n" + ("_" * 4000)

    built = await build_short_term_history(
        store=conversation_store,
        now=datetime.now(timezone.utc),
        window_days=settings.storage.short_term_days,
        context_length=main_ctx_length,
        system_text=system_estimate,
        new_user_text=new_user_content,
        max_tokens_hint=request.max_tokens,
        space_id=space_id,
    )
    logger.debug(
        "  🧵 短期对话装填 (non-stream): %d/%d 条 (预算 %d tok)",
        built.kept, built.total_candidates, built.budget,
    )

    # 用装填后的 history + 本轮新消息替换 state.messages; extracted_new 只放本轮
    combined = list(built.conversation_history)
    if new_user_content:
        combined.append({"role": "user", "content": new_user_content})
    initial_state["messages"] = combined
    initial_state["extracted_new"] = (
        [{"role": "user", "content": new_user_content}] if new_user_content else []
    )

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

    # 回写跨前端流水
    try:
        if new_user_content:
            await conversation_store.append(
                role="user",
                content=new_user_content,
                token_count=token_count_for_storage(new_user_content),
                source_frontend=source_frontend,
                actor_id=actor_id,
                space_id=space_id,
                external_event_id=external_event_id,
            )
        if response_text:
            await conversation_store.append(
                role="assistant",
                content=response_text,
                token_count=token_count_for_storage(response_text),
                source_frontend=source_frontend,
                actor_id=actor_id,
                space_id=space_id,
            )
    except Exception as e:
        logger.warning("回写 conversation_turns 失败 (不影响响应): %s", e)

    # 幂等缓存: 首次成功响应落库, 平台重发时原样重放
    await _record_idempotency(
        http_request, api_key_id, external_event_id, response_id, response_text,
    )

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
    source_user = initial_state.get("source_user") or ""
    source_frontend = initial_state.get("source_frontend")
    actor_id = initial_state.get("actor_id")
    space_id = initial_state.get("space_id")
    external_event_id = initial_state.get("external_event_id")
    api_key_id = initial_state.get("api_key_id")
    main_candidate = await _resolve_main_candidate(http_request)
    main_model = main_candidate.model if main_candidate else "mnemosync-any"
    main_ctx_length = main_candidate.context_length if main_candidate else None
    multi_forwarder = _get_multi_forwarder(http_request)
    conversation_store = _get_conversation_store(http_request)

    logger.debug("🧠 加载记忆上下文...")
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))

    perms = await memory_store.list_permanent(
        source_user, limit=settings.memory.permanent_load_top
    )
    logger.debug("  📚 永久记忆: %d 条", len(perms))

    # 客户端 history 视为"不可信": 服务器有自己的跨前端流水. 只从本轮请求
    # 中取"最后一条 user 消息"作为新输入; 其余客户端消息忽略。
    client_messages = initial_state.get("messages", [])
    new_user_content = ""
    for m in reversed(client_messages):
        if m.get("role") == "user":
            new_user_content = m.get("content", "")
            break

    retrieved_entries: list = []
    if new_user_content:
        retriever = MemoryRetriever(multi_forwarder, vector_store, memory_store)
        results = await retriever.search(
            new_user_content, top_k=settings.memory.retrieval_top_k,
            source_user=source_user,
        )
        for r in results:
            await memory_store.mark_accessed(r.memory_id)
            entry = await memory_store.get_by_id(r.memory_id)
            if entry:
                retrieved_entries.append(entry)
        logger.debug("  🔍 检索结果: %d 条", len(retrieved_entries))

    rel = await memory_store.get_relationship("default", source_user) if source_user else None
    logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")

    # 4. 代理推理 (可选, 同步, 与检索串行)
    reasoning_text: str | None = None
    if use_proxy_thinking:
        logger.debug("🤔 [代理推理] 开始 (ASSIST role)")
        try:
            perms_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"
            reasoning_text = await run_proxy_thinking(
                forwarder=multi_forwarder,
                user_name=source_user,
                relationship=format_relationship(rel) if rel else "新用户",
                memories=perms_text,
                user_message=new_user_content,
                tools=None,
            )
            logger.debug("  ✅ 代理推理完成, 长度: %d", len(reasoning_text) if reasoning_text else 0)
        except Exception as e:
            logger.warning("代理推理失败, 退化为普通转发: %s", e)
            reasoning_text = None

    # 5. 装填: 服务器侧跨前端对话流水 → 双窗口裁剪 → 拼装 messages
    persona = initial_state.get("persona") or settings.persona.prompt
    persona_name = initial_state.get("persona_name") or settings.persona.name

    system_text = render_main_dialogue_system(
        persona_prompt=persona,
        persona_name=persona_name,
        user_name=source_user,
        permanent_memories=perms,
        retrieved_memories=retrieved_entries,
        relationship=rel,
        proxy_thinking_result=reasoning_text,
    )
    built = await build_short_term_history(
        store=conversation_store,
        now=datetime.now(timezone.utc),
        window_days=settings.storage.short_term_days,
        context_length=main_ctx_length,
        system_text=system_text,
        new_user_text=new_user_content,
        max_tokens_hint=request.max_tokens,
        space_id=space_id,
    )
    logger.debug(
        "  🧵 短期对话装填: %d/%d 条 (预算 %d tok, 已用 %d, 因预算丢弃 %d)",
        built.kept, built.total_candidates, built.budget, built.used, built.dropped_by_budget,
    )

    # 拼装最终 messages: system + trimmed 跨前端历史 + 本轮新用户
    conversation_history = list(built.conversation_history)
    if new_user_content:
        conversation_history.append({"role": "user", "content": new_user_content})

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
            with use_agent("main_dialogue_stream"):
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

        # 组装 assistant 回复文本 (从 SSE chunks 反解)
        assistant_text = parse_sse_stream(collected_chunks) or ""

        # 回写跨前端流水: 先 user 再 assistant, 保序 (append 用 UTC now)
        try:
            if new_user_content:
                await conversation_store.append(
                    role="user",
                    content=new_user_content,
                    token_count=token_count_for_storage(new_user_content),
                    source_frontend=source_frontend,
                    actor_id=actor_id,
                    space_id=space_id,
                    external_event_id=external_event_id,
                )
            if assistant_text:
                await conversation_store.append(
                    role="assistant",
                    content=assistant_text,
                    token_count=token_count_for_storage(assistant_text),
                    source_frontend=source_frontend,
                    actor_id=actor_id,
                    space_id=space_id,
                )
        except Exception as e:
            logger.warning("回写 conversation_turns 失败 (不影响响应): %s", e)

        # 幂等缓存: 首次成功响应落库
        await _record_idempotency(
            http_request, api_key_id, external_event_id, chatcmpl_id, assistant_text,
        )

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
