"""OpenAI 兼容的转发 API 路由.

对外提供 /v1/chat/completions 和 /v1/models.
接收请求 → API Key 验证 → 构建初始 state → 编译图 ainvoke → 返回响应.
流式: 加载记忆 → 构建上下文 → 转发给上游 → 异步触发记忆图.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request

from src.api.reasoning_control import should_use_proxy_thinking
from src.api.schemas.forward import (
    ChatCompletionRequest,
    ModelInfo,
    ModelList,
)
from src.api.tool_policies import (
    filter_client_tools,
)
from src.core.config import get_settings
from src.core.constants import VIRTUAL_MODEL_ANY, VIRTUAL_MODEL_CREATED_AT
from src.infra.debug_context import emit_pipeline
from src.infra.space_lock import SpaceLockManager

# Accessor 单例 (独立模块, 避免循环导入)
from . import _accessors as _acc  # noqa: F401  (re-exported for backward compat)
from ._accessors import (  # noqa: F401 – re-exported for backward compat
    _build_graph_config,
    _get_api_key_store,
    _get_compiled_graph,
    _get_conversation_store,
    _get_debug_bus,
    _get_idempotency_store,
    _get_identity_store,
    _get_multi_forwarder,
    _get_persona_store,
    _get_plugins,
)

# 子模块
from .dispatch import (
    _build_initial_state,
    _extract_and_resolve_tool_transaction,
    _handle_identity_binding,
    _normalize_messages,
    _prepare_prompt,
    _resolve_tool_policy,
    _validate_model,
)
from .idempotency import (
    _lookup_idempotency,
    _replay_json_response,
    _replay_stream_response,
)
from .identity import (
    _model_speaker_label,
    _resolve_identity_context,
    _resolve_main_model,
    _resolve_source_frontend,
    _verify_api_key,
)
from .nonstream import _handle_non_stream
from .persistence import (
    _persist_plugin_events,
)
from .stream import _handle_stream

router = APIRouter(prefix="/v1")
logger = logging.getLogger(__name__)

# ── Models ─────────────────────────────────────────────────────


@router.get("/models", response_model=ModelList, tags=["Models"])
async def list_models():
    """列出可用模型."""
    return ModelList(
        object="list",
        data=[
            ModelInfo(
                id=VIRTUAL_MODEL_ANY,
                object="model",
                created=VIRTUAL_MODEL_CREATED_AT,
                owned_by="mnemosync",
            )
        ],
    )


@router.get("/models/{model_id}", response_model=ModelInfo, tags=["Models"])
async def get_model(model_id: str):
    """获取特定模型信息."""
    if model_id == VIRTUAL_MODEL_ANY:
        return ModelInfo(
            id=VIRTUAL_MODEL_ANY,
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
    logger.debug("  model: %s  stream: %s  temp: %s  max_tokens: %s  msgs: %d",
                 request.model, request.stream, request.temperature,
                 request.max_tokens, len(request.messages))

    # 1. 模型验证
    _validate_model(request.model)

    # 2. API Key 验证 + source_frontend 派生
    api_key = await _verify_api_key(http_request)
    api_key_id = api_key.id if api_key else None
    source_frontend = await _resolve_source_frontend(http_request, api_key_id)
    if source_frontend:
        logger.debug("  source_frontend: %s", source_frontend)

    # 3. 工具策略加载
    tool_policy = await _resolve_tool_policy(http_request, api_key)

    # 4. 消息规范化 + 工具事务提取
    messages_dict = _normalize_messages(request.messages)
    tool_transaction, interaction_id = await _extract_and_resolve_tool_transaction(
        messages_dict, request.tools, http_request,
    )

    # 5. 身份解析
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
        external_event_id = http_request.headers.get("Idempotency-Key") or None

    # 6. 插件预处理
    from dataclasses import replace as _replace
    preprocess_result = None
    if api_key and api_key.strategy_id:
        identity_store = _get_identity_store(http_request)
        if identity_store:
            strategy = await identity_store.get_strategy(api_key.strategy_id)
            if strategy and strategy.strategy_type == "plugin":
                import json as _json
                cfg = _json.loads(strategy.config) if strategy.config else {}
                plugin_name = cfg.get("plugin_name", "")
                plugins = _get_plugins(http_request)
                plugin = plugins.get(plugin_name)
                if plugin and identity_ctx:
                    preprocess_result = await plugin.preprocess(
                        messages_dict, cfg, identity_store, identity_ctx,
                    )
                    messages_dict = preprocess_result.model_messages
                    current_event = preprocess_result.current_event
                    if tool_transaction:
                        if current_event:
                            tool_transaction = _replace(
                                tool_transaction,
                                root_user_content=current_event.content,
                            )
                        preprocess_result = type(preprocess_result)(
                            model_messages=messages_dict, events=[],
                        )
                        external_event_id = None
                    elif current_event and current_event.external_event_id:
                        external_event_id = current_event.external_event_id
                    logger.debug(
                        "  plugin preprocess (%s): %d messages, %d events",
                        plugin_name, len(messages_dict), len(preprocess_result.events),
                    )

    # 工具续轮无新事件; 不复用根事件幂等键
    if tool_transaction:
        external_event_id = None

    # 7. 幂等检查
    if external_event_id:
        replay = await _lookup_idempotency(http_request, api_key_id, external_event_id)
        if replay is not None:
            logger.info("idempotency hit (%s), replaying cached response", external_event_id)
            replay_model = await _resolve_main_model(http_request)
            if request.stream:
                return _replay_stream_response(replay, replay_model)
            return _replay_json_response(replay, replay_model)

    # 8. 历史快照持久化
    event_request_id = f"req-{uuid.uuid4().hex[:16]}"
    if preprocess_result:
        history_events = [
            e for e in preprocess_result.events if e.origin == "history_snapshot"
        ]
        await _persist_plugin_events(
            _get_conversation_store(http_request), history_events, event_request_id,
        )

    # 9. 服务器优先人格
    settings = get_settings()
    persona_definition = None
    persona_store = _get_persona_store(http_request)
    if persona_store is not None:
        try:
            persona_definition = await persona_store.get_active()
        except Exception:
            pass
    persona = settings.persona.prompt
    persona_name = settings.persona.name
    if persona_definition is not None:
        persona_name = persona_definition.name
    logger.debug("  persona: %s (structured=%s)", persona_name, persona_definition is not None)

    # 10. 提示词清洗
    persona, prompt_cleaning_result = await _prepare_prompt(
        request.messages, persona, http_request,
    )

    # 11. 入站工具过滤 + 内部工具注入
    allowed_tools = filter_client_tools(request.tools, tool_policy)
    from src.core.tools.internal_registry import get_internal_tool_registry
    internal_registry = get_internal_tool_registry()
    internal_tool_names: set[str] = set()
    if not internal_registry.is_empty() and not tool_transaction and not request.stream:
        internal_tools = internal_registry.to_openai_tools()
        allowed_tools = (allowed_tools or []) + internal_tools
        internal_tool_names = internal_registry.names
        logger.debug("  injected %d internal tools", len(internal_tools))

    # 12. 身份绑定指令处理 (可能提前返回)
    if not tool_transaction and actor_id:
        bind_response = await _handle_identity_binding(
            http_request, request, messages_dict,
            actor_id, space_id, current_speaker,
        )
        if bind_response is not None:
            return bind_response

    # 13. 模型解析 + 代理推理
    main_model = await _resolve_main_model(
        http_request,
        require_tools=bool(allowed_tools),
        streaming=bool(request.stream),
    )
    use_proxy = should_use_proxy_thinking(request, settings, main_model=main_model)
    if tool_transaction:
        use_proxy = False
    logger.debug("  proxy_thinking: %s (main_model=%s)",
                 "enabled" if use_proxy else "skipped", main_model)

    # 14. 工具续轮验证 + 调试事件
    if tool_transaction and allowed_tools is not request.tools:
        allowed_names = {f.get("function", {}).get("name") for f in (allowed_tools or [])}
        for msg in tool_transaction.messages:
            for call in msg.get("tool_calls") or []:
                name = call.get("function", {}).get("name", "")
                if name and name not in allowed_names:
                    raise HTTPException(400, f"tool transaction uses forbidden tool: {name}")
    if request.tools and allowed_tools != request.tools:
        original_names = [
            t.get("function", {}).get("name", "") for t in (request.tools or [])
        ]
        kept_names = [
            t.get("function", {}).get("name", "") for t in (allowed_tools or [])
        ]
        removed = [n for n in original_names if n and n not in kept_names]
        emit_pipeline(
            _get_debug_bus(http_request),
            event_kind="tool_policy", stage="inbound",
            original_tools=original_names, kept_tools=kept_names,
            removed_tools=removed or None,
        )

    # 15. 表达习惯提取
    expression_style = ""
    if channel_type == "group" and not tool_transaction:
        try:
            from src.core.memory.expression_style import extract_style_from_turns
            conv_store = _get_conversation_store(http_request)
            recent_turns, _ = await conv_store.list_page(
                limit=20, offset=0, role="assistant", space_id=space_id,
                event_type="message", sort_by="ts", sort_order="desc",
            )
            style = extract_style_from_turns(recent_turns, space_id or "")
            expression_style = style.to_memory_content()
        except Exception:
            pass

    # 16. 构建初始状态 + 空间锁 + 派发
    initial_state = _build_initial_state(
        messages_dict=messages_dict, allowed_tools=allowed_tools,
        request=request, tool_transaction=tool_transaction,
        tool_policy=tool_policy, expression_style=expression_style,
        interaction_id=interaction_id, internal_tool_names=internal_tool_names,
        source_user=source_user, current_speaker=current_speaker,
        actor_id=actor_id, persona=persona, persona_name=persona_name,
        persona_definition=persona_definition, use_proxy=use_proxy,
        main_model=main_model, source_frontend=source_frontend,
        space_id=space_id, channel_type=channel_type,
        external_event_id=external_event_id, api_key_id=api_key_id,
        preprocess_result=preprocess_result,
        prompt_cleaning_result=prompt_cleaning_result,
    )

    from src.api.deps import _state
    space_locks: SpaceLockManager = _state(http_request).space_locks
    lock_key = space_locks.lock_key(
        space_id=space_id, source_user=source_user, api_key_id=api_key_id,
    )
    lock = await space_locks.acquire(lock_key)
    await lock.acquire()
    logger.debug("acquired space lock: %s", lock_key)
    initial_state["_space_lock"] = lock
    initial_state["_space_lock_key"] = lock_key
    try:
        if request.stream:
            return await _handle_stream(http_request, initial_state, request, use_proxy)
        else:
            return await _handle_non_stream(http_request, initial_state, request)
    finally:
        if not request.stream:
            lock.release()
            logger.debug("released space lock: %s", lock_key)
