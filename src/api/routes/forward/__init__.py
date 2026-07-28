"""OpenAI 兼容的转发 API 路由.

对外提供 /v1/chat/completions 和 /v1/models.
接收请求 → API Key 验证 → 构建初始 state → 编译图 ainvoke → 返回响应.
流式: 加载记忆 → 构建上下文 → 转发给上游 → 异步触发记忆图.
"""

import json as _json
import logging
import uuid
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.api.reasoning_control import should_use_proxy_thinking
from src.api.schemas.forward import (
    ChatCompletionRequest,
    ModelInfo,
    ModelList,
)
from src.api.tool_policies import (
    ToolPolicy,
    filter_client_tools,
    load_tool_policy,
)
from src.api.tool_transactions import (
    ToolTransactionError,
    extract_tool_transaction_tail,
)
from src.core.agents import run_prompt_cleaning
from src.core.config import get_settings
from src.core.constants import DEFAULT_PERSONA_ID, VIRTUAL_MODEL_ANY
from src.core.graph import build_graph
from src.infra.debug_context import emit_pipeline
from src.infra.forwarder.multi import MultiForwarder
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.conversation_store import SqliteConversationStore
from src.persistence.idempotency_store import SqliteIdempotencyStore
from src.persistence.identity_store import SqliteIdentityStore

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


def _get_plugins(http_request: Request) -> dict[str, Any]:
    """从 app.state 取已加载的插件注册表."""
    return getattr(http_request.app.state, "identity_plugins", {})


def _get_idempotency_store(http_request: Request) -> SqliteIdempotencyStore | None:
    """从 app.state 取共享 SqliteIdempotencyStore (由 lifespan 建立)."""
    return getattr(http_request.app.state, "idempotency_store", None)


def _get_debug_bus(http_request: Request):
    """从 app.state 取 DebugEventBus (可能为 None)."""
    return getattr(http_request.app.state, "debug_bus", None)


def _get_persona_store(http_request: Request):
    """从 app.state 取 SqlitePersonaStore (可能为 None)."""
    return getattr(http_request.app.state, "persona_store", None)


def _build_graph_config(http_request: Request) -> dict[str, Any]:
    """构建 LangGraph config["configurable"], 注入共享 store 单例.

    节点通过 ``_get_stores(config)`` 从 config 中取出长连接 store,
    避免每次节点执行新建 SQLite 连接. 测试环境下缺失的属性自动跳过
    (节点回退到懒加载).
    """
    state = http_request.app.state
    configurable: dict[str, Any] = {}
    for key in ("multi_forwarder", "resolver", "memory_store",
                "vector_store", "notification_store", "debug_bus",
                "identity_store", "persona_store", "lorebook_store"):
        val = getattr(state, key, None)
        if val is not None:
            configurable[key] = val
    return {"configurable": configurable}


# ── 子模块导入 (在 accessor 函数之后, 确保循环导入可用) ──────────

from .idempotency import (  # noqa: E402
    _lookup_idempotency,
    _replay_json_response,
    _replay_stream_response,
)
from .identity import (  # noqa: E402
    _model_speaker_label,
    _resolve_identity_context,
    _resolve_main_model,
    _resolve_source_frontend,
    _verify_api_key,
)
from .nonstream import _handle_non_stream  # noqa: E402
from .persistence import (  # noqa: E402
    _persist_plugin_events,
)
from .stream import _handle_stream  # noqa: E402

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
                created=1686935002,
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
    logger.debug("📥 收到 chat/completions 请求")
    logger.debug("  model: %s", request.model)
    logger.debug("  stream: %s", request.stream)
    logger.debug("  temperature: %s", request.temperature)
    logger.debug("  max_tokens: %s", request.max_tokens)
    logger.debug("  messages count: %d", len(request.messages))

    # 验证模型名称: 只接受 mnemosync-any 或空（使用默认模型）
    if request.model and request.model != VIRTUAL_MODEL_ANY:
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
        emit_pipeline(
            _get_debug_bus(http_request),
            event_kind="tool_transaction",
            tail_messages=len(tool_transaction.messages),
            interaction_id=interaction_id,
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
    preprocess_result = None
    if api_key and api_key.strategy_id:
        identity_store = _get_identity_store(http_request)
        if identity_store:
            strategy = await identity_store.get_strategy(api_key.strategy_id)
            if strategy and strategy.strategy_type == "plugin":
                cfg = _json.loads(strategy.config) if strategy.config else {}
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
                        preprocess_result = type(preprocess_result)(
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

    # 服务器优先人格: 从 DB 加载结构化定义, 不从客户端 system 消息提取
    settings = get_settings()
    persona_definition = None
    persona_store = _get_persona_store(http_request)
    if persona_store is not None:
        try:
            persona_definition = await persona_store.get_active()
        except Exception:
            pass
    persona = settings.persona.prompt  # legacy fallback
    persona_name = settings.persona.name
    if persona_definition is not None:
        persona_name = persona_definition.name
    logger.debug("  服务器人格: %s (结构化=%s)", persona_name, persona_definition is not None)

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

    # 入站工具过滤: 在模型看到之前移除策略禁止的工具
    allowed_tools = filter_client_tools(request.tools, tool_policy)

    # 注入内部 tools (身份绑定等), 与客户端 tools 合并
    # 仅非流式: 流式无法拦截已发出的 SSE 帧重调 LLM
    from src.core.tools.internal_registry import get_internal_tool_registry
    internal_registry = get_internal_tool_registry()
    internal_tool_names: set[str] = set()
    if not internal_registry.is_empty() and not tool_transaction and not request.stream:
        internal_tools = internal_registry.to_openai_tools()
        allowed_tools = (allowed_tools or []) + internal_tools
        internal_tool_names = internal_registry.names
        logger.debug("  🔧 注入 %d 个内部 tool", len(internal_tools))

    # 指令触发: 身份绑定 (可自定义指令词)
    if not tool_transaction and actor_id:
        bind_cmd = settings.runtime.identity_bind_command
        bind_prefix = settings.runtime.identity_bind_confirm_prefix
        last_user_msg = ""
        for m in reversed(messages_dict):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "").strip()
                break
        if last_user_msg == bind_cmd:
            # 发起绑定: 生成验证码
            from src.core.tools.identity_binding import get_binding_code_store
            code_store = get_binding_code_store()
            code = await code_store.generate(
                actor_id=actor_id, space_id=space_id, display_name=current_speaker,
            )
            from fastapi.responses import JSONResponse
            return JSONResponse(content={
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "model": main_model if (main_model := await _resolve_main_model(http_request)) else VIRTUAL_MODEL_ANY,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"跨平台绑定验证码: {code}\n请在另一端发送「{bind_prefix} {code}」完成绑定。验证码 5 分钟内有效。",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
        if last_user_msg.startswith(bind_prefix + " ") and len(last_user_msg.split()) == 2:
            # 确认绑定: 校验验证码
            input_code = last_user_msg.split(None, 1)[1].strip()
            from src.core.tools.identity_binding import get_binding_code_store
            code_store = get_binding_code_store()
            entry = await code_store.verify(input_code)
            if entry is None:
                from fastapi.responses import JSONResponse
                return JSONResponse(content={
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "model": VIRTUAL_MODEL_ANY,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "验证码无效或已过期。"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })
            # 执行绑定
            identity_store = _get_identity_store(http_request)
            if identity_store:
                target_actor_id = entry["actor_id"]
                target_groups = await identity_store.list_actor_groups(target_actor_id)
                current_groups = await identity_store.list_actor_groups(actor_id)
                if current_groups:
                    msg = "当前账号已绑定, 无法重复绑定。"
                elif target_groups:
                    group_id = target_groups[0].id
                    await identity_store.bind_actor_to_group(actor_id, group_id)
                    msg = f"绑定成功! 已加入用户组 {group_id}。"
                else:
                    group = await identity_store.create_group(name=None)
                    await identity_store.bind_actor_to_group(target_actor_id, group.id)
                    await identity_store.bind_actor_to_group(actor_id, group.id)
                    msg = f"绑定成功! 已创建新用户组 {group.id}。"
            else:
                msg = "身份存储不可用, 绑定失败。"
            from fastapi.responses import JSONResponse
            return JSONResponse(content={
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "model": VIRTUAL_MODEL_ANY,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": msg},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })

    main_model = await _resolve_main_model(
        http_request,
        require_tools=bool(allowed_tools),
        streaming=bool(request.stream),
    )
    use_proxy = should_use_proxy_thinking(request, settings, main_model=main_model)
    if tool_transaction:
        # 工具续轮已经包含 MAIN 的上一轮决策和客户端执行结果；代理推理会
        # 在看不到完整事务语义时产生重复或冲突建议。
        use_proxy = False
    logger.debug("  代理推理: %s (main_model=%s)", "启用" if use_proxy else "跳过", main_model)

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

    # 调试事件: 工具策略入站过滤
    if request.tools and allowed_tools != request.tools:
        original_names = [
            t.get("function", {}).get("name", "") for t in request.tools
        ] if request.tools else []
        kept_names = [
            t.get("function", {}).get("name", "") for t in (allowed_tools or [])
        ] if allowed_tools else []
        removed = [n for n in original_names if n and n not in kept_names]
        emit_pipeline(
            _get_debug_bus(http_request),
            event_kind="tool_policy",
            stage="inbound",
            original_tools=original_names,
            kept_tools=kept_names,
            removed_tools=removed or None,
        )

    # 提取表达习惯 (群聊时, 从最近 assistant 回复提取)
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

    initial_state = {
        "messages": messages_dict,
        "tools": allowed_tools,
        "tool_choice": request.tool_choice if allowed_tools else None,
        "parallel_tool_calls": request.parallel_tool_calls if allowed_tools else None,
        "tool_transaction": tool_transaction,
        "tool_policy": tool_policy,
        "expression_style": expression_style,
        "interaction_id": interaction_id,
        "internal_tool_names": internal_tool_names,
        "source_user": source_user,
        "current_speaker": current_speaker,
        "actor_id": actor_id,
        "persona": persona,
        "persona_name": persona_name,
        "persona_id": DEFAULT_PERSONA_ID,
        "persona_definition": persona_definition,
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

    # 同空间串行: 同一空间内的请求逐条处理, 不同空间并行
    from src.infra.space_lock import SpaceLockManager
    space_locks: SpaceLockManager = http_request.app.state.space_locks
    lock_key = space_locks.lock_key(
        space_id=space_id, source_user=source_user, api_key_id=api_key_id,
    )
    lock = await space_locks.acquire(lock_key)
    await lock.acquire()
    logger.debug("🔒 获取空间锁: %s", lock_key)
    initial_state["_space_lock"] = lock
    initial_state["_space_lock_key"] = lock_key
    try:
        if request.stream:
            return await _handle_stream(http_request, initial_state, request, use_proxy)
        else:
            return await _handle_non_stream(http_request, initial_state, request)
    finally:
        if not request.stream:
            # 非流式: 响应已生成, 释放锁
            # 流式: 锁在 stream_generator 的 finally 中释放 (见 stream.py)
            lock.release()
            logger.debug("🔓 释放空间锁: %s", lock_key)
