"""OpenAI 兼容的转发 API 路由.

对外提供 /v1/chat/completions 和 /v1/models.
接收请求 → API Key 验证 → 构建初始 state → 编译图 ainvoke → 返回响应.
流式: 加载记忆 → 构建上下文 → 转发给上游 → 异步触发记忆图.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.reasoning_control import (
    build_reasoning_stream_frames,
    chunk_has_native_reasoning,
    mark_native_reasoning,
    should_use_proxy_thinking,
)
from src.api.schemas.forward import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ModelInfo,
    ModelList,
    UsageInfo,
)
from src.api.tool_policies import (
    ToolPolicy,
    filter_client_tools,
    filter_tool_calls,
    load_tool_policy,
    validate_tool_arguments,
)
from src.api.tool_transactions import (
    ToolTransactionError,
    append_tool_transaction_context,
    extract_tool_transaction_tail,
)
from src.core.agents import run_prompt_cleaning, run_proxy_thinking
from src.core.config import get_settings
from src.core.graph import build_graph
from src.core.identity import IdentityContext, IdentityResolver
from src.core.identity.plugin import IdentityPlugin, NormalizedEvent, PluginPreprocessResult
from src.core.memory import format_relationship
from src.core.memory.context import (
    build_main_dialogue_messages,
    render_main_dialogue_system,
)
from src.core.memory.short_term import (
    build_short_term_history,
    token_count_for_storage,
)
from src.core.models.resolver import NoCandidateForRoleError
from src.infra.debug_context import use_agent
from src.infra.forwarder import (
    UpstreamError,
    UpstreamTimeout,
    parse_sse_stream_full,
)
from src.infra.forwarder.multi import (
    MultiForwarder,
    UpstreamAllCandidatesFailed,
)
from src.infra.llm_service.models import ModelType
from src.infra.vector_store import VectorStore
from src.persistence.api_key_store import ApiKey, SqliteApiKeyStore
from src.persistence.conversation_store import (
    ConversationEvent,
    SqliteConversationStore,
    build_event_fingerprint,
)
from src.persistence.idempotency_store import IdempotencyRecord, SqliteIdempotencyStore
from src.persistence.identity_store import SqliteIdentityStore
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


def _get_plugins(http_request: Request) -> dict[str, IdentityPlugin]:
    """从 app.state 取已加载的插件注册表."""
    return getattr(http_request.app.state, "identity_plugins", {})


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

    async def replay_generator():
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
    plugins = _get_plugins(http_request)
    resolver = IdentityResolver(identity_store, forwarder, plugins)
    return await resolver.resolve(
        request_user=request_user,
        messages=messages,
        strategy_type=strategy.strategy_type,
        strategy_config=config,
        strategy_name=strategy.name,
    )


def _model_speaker_label(
    identity: IdentityContext | None,
    request_user: str | None,
) -> str:
    """生成模型可读身份；内部 actor/group UUID 只用于存储，绝不进入提示词."""
    if identity is None:
        return (request_user or "未知参与者").strip() or "未知参与者"
    name = (identity.display_name or "").strip()
    external_key = (identity.external_key or "").strip()
    frontend = (identity.frontend or "unknown").strip()
    if name and external_key:
        return f"{name} | {frontend} {external_key}"
    return name or external_key or "未知参与者"


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


def _conversation_events(
    normalized: list[NormalizedEvent],
    request_id: str,
) -> list[ConversationEvent]:
    """将插件事件转为带稳定指纹的存储事件."""
    events: list[ConversationEvent] = []
    for item in normalized:
        event = ConversationEvent(
            role=item.role,
            content=item.content,
            token_count=token_count_for_storage(item.content),
            source_frontend=item.source_frontend,
            ts=item.source_timestamp,
            actor_id=item.actor_id,
            effective_user_id=item.effective_user_id,
            display_name_snapshot=item.display_name,
            external_key_snapshot=item.external_key,
            space_id=item.space_id,
            external_event_id=item.external_event_id,
            origin=item.origin,
            request_id=request_id,
        )
        if item.origin == "history_snapshot" or item.external_event_id:
            event.event_fingerprint = build_event_fingerprint(event)
        events.append(event)
    return events


async def _persist_plugin_events(
    store: SqliteConversationStore,
    normalized: list[NormalizedEvent],
    request_id: str,
) -> None:
    """批量持久化插件事件；失败不阻断主回复."""
    if not normalized:
        return
    try:
        result = await store.append_events(_conversation_events(normalized, request_id))
        logger.debug(
            "  🧩 规范化事件写入: 新增 %d, 去重 %d",
            result.inserted,
            result.duplicates,
        )
    except Exception as exc:
        logger.warning("写入规范化 conversation events 失败 (不影响响应): %s", exc)


async def _persist_assistant_event(
    store: SqliteConversationStore,
    content: str,
    initial_state: dict[str, Any],
    request_id: str,
    *,
    response_message: dict[str, Any] | None = None,
) -> None:
    if not content and not (response_message and response_message.get("tool_calls")):
        return
    interaction_id = initial_state.get("interaction_id")
    # 持久化 tool_calls 为独立 tool_call 事件 (不混入自然语言流水)
    tool_calls = (response_message or {}).get("tool_calls")
    if tool_calls:
        for call in tool_calls:
            call_id = call.get("id", "")
            func = call.get("function", {})
            if not call_id or not func.get("name"):
                continue
            await store.append(
                role="assistant",
                content=json.dumps(call, ensure_ascii=False),
                token_count=8,
                source_frontend="mnemosync",
                actor_id=None,
                effective_user_id=initial_state.get("source_user"),
                space_id=initial_state.get("space_id"),
                origin="assistant",
                request_id=request_id,
                interaction_id=interaction_id,
                event_type="tool_call",
                tool_call_id=call_id,
            )
    # 文本内容作为 message 事件持久化
    if content:
        await store.append(
            role="assistant",
            content=content,
            token_count=token_count_for_storage(content),
            source_frontend="mnemosync",
            actor_id=None,
            effective_user_id=initial_state.get("source_user"),
            space_id=initial_state.get("space_id"),
            origin="assistant",
            request_id=request_id,
            interaction_id=interaction_id,
            event_type="message",
        )


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

    # 加载工具策略 (从 identity strategy config 的 tool_policy 字段读取)
    tool_policy: ToolPolicy | None = None
    if api_key and api_key.strategy_id:
        identity_store = _get_identity_store(http_request)
        if identity_store:
            try:
                strategy = await identity_store.get_strategy(api_key.strategy_id)
                if strategy and strategy.config:
                    import json as _json
                    cfg = _json.loads(strategy.config)
                    if isinstance(cfg, dict) and "tool_policy" in cfg:
                        tool_policy = load_tool_policy(_json.dumps(cfg["tool_policy"]))
            except Exception:
                pass
    if source_frontend:
        logger.debug("  🔖 source_frontend: %s", source_frontend)

    # 构建初始 state
    messages_dict = [msg.model_dump(exclude_none=True) for msg in request.messages]
    # 兼容 OpenAI content parts 数组格式: 将数组展开为纯文本
    for m in messages_dict:
        content = m.get("content")
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text", "")))
            m["content"] = "\n".join(texts) if texts else ""
        elif content is None:
            m["content"] = ""
    logger.debug("  构建 state 完成, 消息数: %d", len(messages_dict))

    # 客户端工具续轮只信任标准 assistant(tool_calls) → tool 尾部；其余历史仍不可信。
    interaction_id: str | None = None
    try:
        tool_transaction = extract_tool_transaction_tail(messages_dict, request.tools)
    except ToolTransactionError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid tool transaction: {exc}") from exc
    # 工具续轮: 若尾部无内容 (空 tail), 不允许
    if tool_transaction is not None and not tool_transaction.messages:
        raise HTTPException(status_code=400, detail="Invalid tool transaction: 空尾部")
    if tool_transaction:
        # 通过首个 tool_call_id 查回逻辑交互 ID (需查 conversation_store)
        conversation_store = _get_conversation_store(http_request)
        first_call_id = next(
            (
                call["id"]
                for msg in tool_transaction.messages
                for call in (msg.get("tool_calls") or [])
            ),
            None,
        )
        if first_call_id:
            interaction_id = await conversation_store.get_interaction_for_tool_call(first_call_id)
        # 若无历史 interaction_id (首次 tool_calls 未落库或重启后首次续轮), 以首个 tool_call_id 派生
        if interaction_id is None and first_call_id:
            interaction_id = f"tool-{first_call_id[:16]}"
            logger.debug("  🔧 派生 interaction_id: %s", interaction_id)
        logger.debug(
            "  🔧 工具事务尾部: %d 条协议消息 (interaction_id=%s)",
            len(tool_transaction.messages),
            interaction_id or "无",
        )
    if tool_transaction:
        logger.debug(
            "  🔧 工具事务尾部: %d 条协议消息",
            len(tool_transaction.messages),
        )

    # 身份解析：通过 API Key 绑定的策略识别参与者
    identity_ctx = await _resolve_identity_context(
        http_request, api_key, request.user, messages_dict,
    )
    actor_id = identity_ctx.actor_id if identity_ctx else None
    source_user = identity_ctx.effective_user_id if identity_ctx else (request.user or None)
    current_speaker = _model_speaker_label(identity_ctx, request.user)
    space_id = identity_ctx.space_id if identity_ctx else None
    channel_type = identity_ctx.channel_type if identity_ctx else None
    external_event_id = identity_ctx.external_event_id if identity_ctx else None
    if not external_event_id:
        # 可选兜底: 客户端主动带 Idempotency-Key 头时也接受 (不要求客户端适配)
        external_event_id = http_request.headers.get("Idempotency-Key") or None

    # 插件预处理: 强类型返回模型消息 + 逐说话者规范化事件
    preprocess_result: PluginPreprocessResult | None = None
    if api_key and api_key.strategy_id:
        identity_store = _get_identity_store(http_request)
        if identity_store:
            strategy = await identity_store.get_strategy(api_key.strategy_id)
            if strategy and strategy.strategy_type == "plugin":
                cfg = json.loads(strategy.config) if strategy.config else {}
                plugin_name = cfg.get("plugin_name", "")
                plugins = _get_plugins(http_request)
                plugin = plugins.get(plugin_name)
                if plugin and identity_ctx:
                    preprocess_result = await plugin.preprocess(
                        messages_dict,
                        cfg,
                        identity_store,
                        identity_ctx,
                    )
                    messages_dict = preprocess_result.model_messages
                    current_event = preprocess_result.current_event
                    if tool_transaction:
                        # 工具续轮复用根 user 只为身份与内容清洗；不能把它再次视为
                        # 当前平台事件写入，也不能复用根事件幂等键重放首次 tool_calls。
                        if current_event:
                            tool_transaction = replace(
                                tool_transaction,
                                root_user_content=current_event.content,
                            )
                        preprocess_result = PluginPreprocessResult(
                            model_messages=messages_dict,
                            events=[],
                        )
                        external_event_id = None
                    elif current_event and current_event.external_event_id:
                        external_event_id = current_event.external_event_id
                    logger.debug(
                        "  插件预处理完成 (%s): %d 条模型消息, %d 个事件",
                        plugin_name,
                        len(messages_dict),
                        len(preprocess_result.events),
                    )

    # 工具续轮没有新的平台 user 事件；不能复用根消息事件 ID 触发首次响应重放。
    # 工具续轮自身的幂等将在交互事务持久化阶段处理。
    if tool_transaction:
        external_event_id = None

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

    # 平台历史快照必须在上下文装填前进入服务端事件流；当前消息则仅在本轮成功后落库。
    event_request_id = f"req-{uuid.uuid4().hex[:16]}"
    if preprocess_result:
        history_events = [
            event for event in preprocess_result.events
            if event.origin == "history_snapshot"
        ]
        await _persist_plugin_events(
            _get_conversation_store(http_request),
            history_events,
            event_request_id,
        )

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
    if tool_transaction:
        # 工具续轮已经包含 MAIN 的上一轮决策和客户端执行结果；代理推理会
        # 在看不到完整事务语义时产生重复或冲突建议。
        use_proxy = False
    logger.debug("  代理推理: %s (main_model=%s)", "启用" if use_proxy else "跳过", main_model)

    # 入站工具过滤: 在模型看到之前移除策略禁止的工具
    allowed_tools = filter_client_tools(request.tools, tool_policy)
    # 工具续轮必须使用本轮允许的工具; 若续轮涉及已被策略禁止的工具则整轮拒绝
    if tool_transaction and allowed_tools is not request.tools:
        allowed_names = {f.get("function", {}).get("name") for f in (allowed_tools or [])}
        for msg in tool_transaction.messages:
            for call in msg.get("tool_calls") or []:
                name = call.get("function", {}).get("name", "")
                if name and name not in allowed_names:
                    raise HTTPException(400, f"工具续轮使用了被策略禁止的工具: {name}")
    if request.tools and allowed_tools is None:
        logger.debug("  🔧 工具策略: 所有工具被拒绝, 本轮无工具可用")
    elif allowed_tools != request.tools:
        logger.debug("  🔧 工具策略: 入站过滤, 原 %d 工具 → 剩 %d",
                     len(request.tools or []), len(allowed_tools or []))

    initial_state = {
        "messages": messages_dict,
        "tools": allowed_tools,
        "tool_choice": request.tool_choice if allowed_tools else None,
        "parallel_tool_calls": request.parallel_tool_calls if allowed_tools else None,
        "tool_transaction": tool_transaction,
        "tool_policy": tool_policy,
        "interaction_id": interaction_id,
        "source_user": source_user,
        "current_speaker": current_speaker,
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
        "normalized_events": (
            [event for event in preprocess_result.events if event.origin == "current"]
            if preprocess_result else []
        ),
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

    # 提取本轮新用户消息；工具续轮没有新的 user 输入。
    client_messages = initial_state.get("messages", [])
    tool_transaction = initial_state.get("tool_transaction")
    new_user_content = ""
    if not tool_transaction:
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

    budget_input_text = (
        tool_transaction.root_user_content if tool_transaction else new_user_content
    )
    built = await build_short_term_history(
        store=conversation_store,
        now=datetime.now(UTC),
        window_days=settings.storage.short_term_days,
        context_length=main_ctx_length,
        system_text=system_estimate,
        new_user_text=budget_input_text,
        max_tokens_hint=request.max_tokens,
        space_id=space_id,
    )
    initial_state["active_participants"] = built.active_participants
    logger.debug(
        "  🧵 短期对话装填 (non-stream): %d/%d 条 (预算 %d tok)",
        built.kept, built.total_candidates, built.budget,
    )

    # 用装填后的 history + 当前输入替换 state.messages；工具续轮接入校验后的协议尾部。
    combined = list(built.conversation_history)
    if tool_transaction:
        combined = append_tool_transaction_context(combined, tool_transaction)
    elif new_user_content:
        combined.append({"role": "user", "content": new_user_content})
    initial_state["messages"] = combined
    if tool_transaction:
        initial_state["extracted_new"] = [
            {"role": "user", "content": tool_transaction.root_user_content}
        ]
    else:
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
    response_message = final_state.get("response_message")
    finish_reason = final_state.get("finish_reason") or "stop"
    reasoning = final_state.get("proxy_thinking_result") or None
    upstream_usage = final_state.get("upstream_usage") or {}
    response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    main_model = initial_state.get("main_model") or await _resolve_main_model(http_request)

    # 出站工具过滤: 移除模型违反策略生成的工具调用
    policy = initial_state.get("tool_policy")
    removed_calls: list[str] = []
    if response_message and response_message.get("tool_calls"):
        # 确定性隐私检查: UUID 泄露、参数体积、JSON 合法性
        valid_calls, privacy_issues = validate_tool_arguments(
            response_message["tool_calls"], initial_state.get("tools"),
        )
        if privacy_issues:
            logger.debug("  🔧 工具参数检查: 移除 %s", privacy_issues)
            removed_calls.extend(privacy_issues)
        # 策略过滤: 白名单/黑名单/冷却
        if policy:
            kept, policy_removed = filter_tool_calls(valid_calls, policy)
            removed_calls.extend(policy_removed)
            valid_calls = kept
        response_message = {**response_message, "tool_calls": valid_calls or None}
        if removed_calls:
            logger.debug("  🔧 出站过滤, 移除 %s", removed_calls)

    # 使用完整 assistant message 构造响应 (含 tool_calls)
    if response_message:
        message_payload = dict(response_message)
        message_payload["role"] = "assistant"
        if reasoning and not message_payload.get("reasoning_content"):
            message_payload["reasoning_content"] = reasoning
        # 若所有工具调用被策略移除, 降级为普通文本
        if not message_payload.get("content") and not message_payload.get("tool_calls"):
            message_payload["content"] = ""
            if removed_calls:
                message_payload["content"] = "(动作不可用)"
            finish_reason = "stop"
        choice_message = ChatMessage.model_validate(message_payload)
    else:
        choice_message = ChatMessage(
            role="assistant",
            content=response_text,
            reasoning_content=reasoning,
        )

    # 回写结构化事件流。插件事件已拆分历史说话者；普通请求仍只写当前用户。
    request_id = response_id
    try:
        normalized_events = initial_state.get("normalized_events") or []
        if normalized_events:
            await _persist_plugin_events(conversation_store, normalized_events, request_id)
        elif new_user_content:
            await conversation_store.append(
                role="user",
                content=new_user_content,
                token_count=token_count_for_storage(new_user_content),
                source_frontend=source_frontend,
                actor_id=actor_id,
                effective_user_id=source_user or None,
                space_id=space_id,
                external_event_id=external_event_id,
                origin="current",
                request_id=request_id,
            )
        await _persist_assistant_event(
            conversation_store,
            response_text,
            initial_state,
            request_id,
            response_message=response_message,
        )
    except Exception as e:
        logger.warning("回写 conversation_turns 失败 (不影响响应): %s", e)

    # 幂等缓存: 首次成功响应落库, 平台重发时原样重放
    await _record_idempotency(
        http_request, api_key_id, external_event_id, response_id, response_text,
        response_message=response_message,
        finish_reason=finish_reason,
    )

    usage_info = UsageInfo(
        prompt_tokens=int(upstream_usage.get("prompt_tokens", 0)),
        completion_tokens=int(upstream_usage.get("completion_tokens", 0)),
        total_tokens=int(upstream_usage.get("total_tokens", 0)),
    )

    logger.debug("📤 返回响应: %s (finish_reason=%s, reasoning=%s, tool_calls=%s)",
                 response_id, finish_reason, "有" if reasoning else "无",
                 "有" if choice_message.tool_calls else "无")
    response_body = ChatCompletionResponse(
        id=response_id,
        model=main_model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=choice_message,
                finish_reason=finish_reason,
            )
        ],
        usage=usage_info,
    ).model_dump(exclude_none=True)
    # OpenAI 工具调用响应通常显式携带 content: null. 保留该字段避免
    # 严格客户端把缺失 content 误判为非法 assistant message.
    if choice_message.tool_calls and choice_message.content is None:
        response_body["choices"][0]["message"]["content"] = None
    return JSONResponse(content=response_body)


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
    current_speaker = initial_state.get("current_speaker") or "未知参与者"
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

    from src.core.memory.audience import AudienceFilter, RetrievalContext

    rel = await memory_store.get_relationship(
        initial_state.get("persona_id", "default"), source_user,
    ) if source_user else None
    logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")
    retrieval_ctx = RetrievalContext(
        effective_user_id=source_user or None,
        actor_id=actor_id,
        space_id=space_id,
        channel_type=initial_state.get("channel_type"),
        relationship=rel,
    )

    perms = await memory_store.list_permanent(
        source_user or None,
        limit=settings.memory.permanent_load_top,
        space_id=space_id,
    )
    perms = AudienceFilter.filter(perms, retrieval_ctx)
    logger.debug("  📚 永久记忆: %d 条", len(perms))

    # 客户端 history 视为"不可信": 服务器有自己的跨前端流水. 普通请求只取
    # 最后一条 user 消息；工具续轮没有新的 user 输入，只接入已校验事务尾部。
    client_messages = initial_state.get("messages", [])
    tool_transaction = initial_state.get("tool_transaction")
    new_user_content = ""
    if not tool_transaction:
        for m in reversed(client_messages):
            if m.get("role") == "user":
                new_user_content = m.get("content", "")
                break

    retrieval_query = (
        tool_transaction.root_user_content if tool_transaction else new_user_content
    )
    retrieved_entries: list = []
    if retrieval_query:
        retriever = MemoryRetriever(multi_forwarder, vector_store, memory_store)
        results = await retriever.search(
            retrieval_query, top_k=settings.memory.retrieval_top_k,
            retrieval_ctx=retrieval_ctx,
        )
        for r in results:
            await memory_store.mark_accessed(r.memory_id)
            entry = await memory_store.get_by_id(r.memory_id)
            if entry:
                retrieved_entries.append(entry)
        logger.debug("  🔍 检索结果: %d 条", len(retrieved_entries))

    # 4. 代理推理 (可选, 同步, 与检索串行)
    reasoning_text: str | None = None
    if use_proxy_thinking:
        logger.debug("🤔 [代理推理] 开始 (ASSIST role)")
        try:
            perms_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"
            reasoning_text = await run_proxy_thinking(
                forwarder=multi_forwarder,
                user_name=current_speaker,
                relationship=format_relationship(rel) if rel else "新用户",
                memories=perms_text,
                user_message=new_user_content,
                tools=None,
                channel_type=initial_state.get("channel_type"),
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
        user_name=current_speaker,
        permanent_memories=perms,
        retrieved_memories=retrieved_entries,
        relationship=rel,
        proxy_thinking_result=reasoning_text,
        current_speaker=current_speaker,
        channel_type=initial_state.get("channel_type"),
        space_label=space_id,
        active_participants=[],
    )
    budget_input_text = (
        tool_transaction.root_user_content if tool_transaction else new_user_content
    )
    built = await build_short_term_history(
        store=conversation_store,
        now=datetime.now(UTC),
        window_days=settings.storage.short_term_days,
        context_length=main_ctx_length,
        system_text=system_text,
        new_user_text=budget_input_text,
        max_tokens_hint=request.max_tokens,
        space_id=space_id,
    )
    logger.debug(
        "  🧵 短期对话装填: %d/%d 条 (预算 %d tok, 已用 %d, 因预算丢弃 %d)",
        built.kept, built.total_candidates, built.budget, built.used, built.dropped_by_budget,
    )

    # 拼装最终 messages: system + trimmed 跨前端历史 + 当前输入
    conversation_history = list(built.conversation_history)
    if tool_transaction:
        conversation_history = append_tool_transaction_context(
            conversation_history,
            tool_transaction,
        )
    elif new_user_content:
        conversation_history.append({"role": "user", "content": new_user_content})

    messages_with_memory = build_main_dialogue_messages(
        persona_prompt=persona,
        persona_name=persona_name,
        user_name=current_speaker,
        permanent_memories=perms,
        retrieved_memories=retrieved_entries,
        relationship=rel,
        conversation_history=conversation_history,
        proxy_thinking_result=reasoning_text,
        current_speaker=current_speaker,
        channel_type=initial_state.get("channel_type"),
        space_label=space_id,
        active_participants=built.active_participants,
    )
    # 后台记忆图只分析这一逻辑交互的根 user；不重新扫描客户端完整历史。
    if tool_transaction:
        initial_state["extracted_new"] = [
            {"role": "user", "content": tool_transaction.root_user_content}
        ]
    else:
        initial_state["extracted_new"] = (
            [{"role": "user", "content": new_user_content}] if new_user_content else []
        )

    logger.debug("  📝 构建消息数: %d (含记忆上下文)", len(messages_with_memory))

    chatcmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    passthrough: dict[str, Any] = {}
    _optional_fields = (
        "tools", "tool_choice", "response_format",
        "stream_options", "top_p", "stop", "seed", "frequency_penalty",
        "presence_penalty", "logit_bias", "logprobs", "top_logprobs",
        "n", "user", "reasoning_effort", "reasoning", "thinking",
    )
    for _f in _optional_fields:
        _v = getattr(request, _f, None)
        if _v is not None:
            passthrough[_f] = _v
    if request.tools and request.parallel_tool_calls is not None:
        passthrough["parallel_tool_calls"] = request.parallel_tool_calls
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
            yield f'data: {{"error": "{e}"}}\n\n'.encode()
            return
        except UpstreamError as e:
            logger.debug("❌ 流式错误: %s", e.message)
            yield f'data: {{"error": "{e.message}"}}\n\n'.encode()
            return
        except UpstreamAllCandidatesFailed as e:
            logger.debug("❌ 所有候选失败: %s", e)
            yield f'data: {{"error": "all candidates failed: {e}"}}\n\n'.encode()
            return
        except NoCandidateForRoleError as e:
            logger.debug("❌ 无候选: %s", e)
            yield f'data: {{"error": "no candidate: {e}"}}\n\n'.encode()
            return

        if saw_native:
            mark_native_reasoning(main_model)

        # 组装 assistant 回复 (从 SSE chunks 反解)
        stream_result = parse_sse_stream_full(collected_chunks)
        assistant_text = stream_result.text or ""
        assistant_finish_reason = stream_result.finish_reason
        assistant_tool_calls = stream_result.tool_calls

        # 构造 response_message 以支持 tool_calls 持久化
        response_message: dict[str, Any] | None = None
        if assistant_tool_calls:
            response_message = {
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": assistant_tool_calls,
            }

        # 回写结构化事件流
        try:
            normalized_events = initial_state.get("normalized_events") or []
            if normalized_events:
                await _persist_plugin_events(
                    conversation_store,
                    normalized_events,
                    chatcmpl_id,
                )
            elif new_user_content:
                await conversation_store.append(
                    role="user",
                    content=new_user_content,
                    token_count=token_count_for_storage(new_user_content),
                    source_frontend=source_frontend,
                    actor_id=actor_id,
                    effective_user_id=source_user or None,
                    space_id=space_id,
                    external_event_id=external_event_id,
                    origin="current",
                    request_id=chatcmpl_id,
                )
            await _persist_assistant_event(
                conversation_store,
                assistant_text,
                initial_state,
                chatcmpl_id,
                response_message=response_message,
            )
        except Exception as e:
            logger.warning("回写 conversation_turns 失败 (不影响响应): %s", e)

        # 幂等缓存: 首次成功响应落库
        await _record_idempotency(
            http_request, api_key_id, external_event_id, chatcmpl_id, assistant_text,
            response_message=response_message,
            finish_reason=assistant_finish_reason,
        )

        logger.debug("🔄 触发后台记忆图...")
        initial_state["proxy_thinking_enabled"] = False
        if reasoning_text:
            initial_state["proxy_thinking_result"] = reasoning_text
        # 工具调用轮次不触发记忆/关系分析
        if assistant_finish_reason == "tool_calls" and not assistant_text:
            logger.debug("  ⏭️ 工具中间轮 (finish_reason=tool_calls, 无文本), 跳过")
        else:
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
    stream_result = parse_sse_stream_full(stream_chunks)
    response_text = stream_result.text or ""
    initial_state["response"] = response_text
    initial_state["response_chunks"] = stream_chunks
    # 流式响应无法获得完整的 assistant message;
    # 但 finish_reason 可用于判断是否跳过记忆/关系分析
    if stream_result.finish_reason:
        initial_state["finish_reason"] = stream_result.finish_reason

    # 纯工具调用 (无文本) 不需要记忆图, 直接返回
    if not response_text and stream_result.tool_calls:
        logger.debug("  ⏭️ 纯工具调用响应, 跳过记忆图")
        return

    graph = _get_compiled_graph()
    try:
        await graph.ainvoke(initial_state)
    except Exception as e:
        # 后台任务失败仅日志
        import logging
        logging.getLogger(__name__).warning("后台记忆图执行失败: %s", e)
