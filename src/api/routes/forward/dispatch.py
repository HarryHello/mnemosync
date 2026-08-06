"""请求派发辅助: 模型验证、消息规范化、工具事务、提示词清洗、身份绑定、状态构建."""
import json as _json
import logging
import uuid
from typing import Any, cast

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.schemas.forward import ChatCompletionRequest, ChatMessage
from src.api.tool_policies import ToolPolicy, load_tool_policy
from src.api.tool_transactions import (
    ToolTransactionError,
    extract_tool_transaction_tail,
)
from src.core.config import get_settings
from src.core.constants import VIRTUAL_MODEL_ANY
from src.core.utils import last_user_message
from src.infra.debug_context import emit_pipeline

from ._accessors import (
    _get_conversation_store,
    _get_debug_bus,
    _get_identity_store,
    _get_multi_forwarder,
)

logger = logging.getLogger(__name__)


# ── 模型验证 ──────────────────────────────────────────────────


def _validate_model(model: str | None) -> None:
    """验证请求模型名: 只接受 mnemosync-any 或空 (使用默认模型)."""
    if model and model != VIRTUAL_MODEL_ANY:
        logger.debug("  invalid model: %s", model)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model '{model}'. Use 'mnemosync-any' or omit the field.",
        )


# ── 工具策略 ──────────────────────────────────────────────────


async def _resolve_tool_policy(
    http_request: Request,
    api_key: Any,
) -> ToolPolicy | None:
    """从 identity strategy config 的 tool_policy 字段加载工具策略."""
    if not api_key or not api_key.strategy_id:
        return None
    identity_store = _get_identity_store(http_request)
    if not identity_store:
        return None
    try:
        strategy = await identity_store.get_strategy(api_key.strategy_id)
        if strategy and strategy.config:
            cfg = _json.loads(strategy.config)
            if isinstance(cfg, dict) and "tool_policy" in cfg:
                return load_tool_policy(_json.dumps(cfg["tool_policy"]))
    except Exception as e:
        logger.debug("工具策略加载失败: %s", e)
    return None


# ── 消息规范化 ────────────────────────────────────────────────


def _normalize_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """将请求消息转为 dict 列表, 并将 OpenAI content parts 数组格式展开为纯文本."""
    result = [msg.model_dump(exclude_none=True) for msg in messages]
    for m in result:
        content = m.get("content")
        if isinstance(content, list):
            texts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            m["content"] = "\n".join(texts) if texts else ""
        elif content is None:
            m["content"] = ""
    logger.debug("  normalized messages: %d", len(result))
    return result


# ── 工具事务提取 ──────────────────────────────────────────────


async def _extract_and_resolve_tool_transaction(
    messages_dict: list[dict[str, Any]],
    tools: list[Any] | None,
    http_request: Request,
) -> tuple[Any, str | None]:
    """提取工具事务尾部并解析 interaction_id.

    Returns:
        (tool_transaction, interaction_id) 元组.
    Raises:
        HTTPException: 事务格式无效时.
    """
    try:
        tool_transaction = extract_tool_transaction_tail(messages_dict, tools)
    except ToolTransactionError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid tool transaction: {exc}") from exc

    if tool_transaction is not None and not tool_transaction.messages:
        raise HTTPException(status_code=400, detail="Invalid tool transaction: empty tail")

    interaction_id: str | None = None
    if tool_transaction:
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
        if interaction_id is None and first_call_id:
            interaction_id = f"tool-{first_call_id[:16]}"
            logger.debug("  derived interaction_id: %s", interaction_id)
        logger.debug(
            "  tool transaction tail: %d messages (interaction_id=%s)",
            len(tool_transaction.messages),
            interaction_id or "none",
        )
        emit_pipeline(
            _get_debug_bus(http_request),
            event_kind="tool_transaction",
            tail_messages=len(tool_transaction.messages),
            interaction_id=interaction_id,
        )

    return tool_transaction, interaction_id


# ── 提示词清洗 + 内部工具 ────────────────────────────────────


async def _prepare_prompt(
    request_messages: list[ChatMessage],
    persona: str,
    http_request: Request,
) -> tuple[str, dict[str, Any] | None]:
    """提取客户端 system 消息并执行提示词清洗.

    Returns:
        (persona, prompt_cleaning_result) — persona 已追加清洗后指令;
        prompt_cleaning_result 为 None 表示无清洗动作.
    """
    client_system_msg = ""
    for msg in request_messages:
        if msg.role == "system" and msg.content:
            client_system_msg = cast(str, msg.content)
            break

    if not client_system_msg.strip():
        return persona, None

    logger.debug("  cleaning client system message (length: %d)", len(client_system_msg))
    multi_forwarder = _get_multi_forwarder(http_request)
    try:
        from src.api.deps import _state as _get_state
        from src.core.agents import run_prompt_cleaning
        from src.core.agents.tracking import run_agent_tracked
        _st = _get_state(http_request)
        cleaning_out = await run_agent_tracked(
            "prompt_cleaning",
            run_prompt_cleaning(
                forwarder=multi_forwarder,
                system_message=client_system_msg,
            ),
            store=_st.agent_run_store,
            debug_bus=_st.debug_bus,
        )
        result = {
            "clean_prompt": cleaning_out.clean_prompt,
            "reasoning": cleaning_out.reasoning,
        }
        if cleaning_out.clean_prompt:
            persona = persona + "\n\n" + cleaning_out.clean_prompt
            logger.debug("  cleaned: output length %d", len(cleaning_out.clean_prompt))
        else:
            logger.debug("  cleaned: no retainable instructions, all discarded")
        return persona, result
    except Exception as e:
        logger.warning("prompt cleaning failed, degrading: discard all client system (%s)", e)
        return persona, {"clean_prompt": "", "reasoning": str(e)}


# ── 身份绑定处理 ──────────────────────────────────────────────


async def _handle_identity_binding(
    http_request: Request,
    request: ChatCompletionRequest,
    messages_dict: list[dict[str, Any]],
    actor_id: str | None,
    space_id: str | None,
    current_speaker: str,
) -> "JSONResponse | None":
    """处理身份绑定指令: 发起绑定 / 确认绑定.

    如果当前请求是绑定指令, 返回 JSONResponse; 否则返回 None 继续正常流程.
    """

    settings = get_settings()
    bind_cmd = settings.runtime.identity_bind_command
    bind_prefix = settings.runtime.identity_bind_confirm_prefix

    if not actor_id:
        return None

    last_user_msg = last_user_message(messages_dict).strip()

    if last_user_msg == bind_cmd:
        return await _bind_initiate(
            http_request, actor_id, space_id, current_speaker, bind_prefix,
        )

    if last_user_msg.startswith(bind_prefix + " ") and len(last_user_msg.split()) == 2:
        input_code = last_user_msg.split(None, 1)[1].strip()
        return await _bind_confirm(
            http_request, input_code, actor_id,
        )

    return None


async def _bind_initiate(
    http_request: Request,
    actor_id: str,
    space_id: str | None,
    current_speaker: str,
    bind_prefix: str,
) -> JSONResponse:
    """发起跨平台绑定: 生成验证码."""
    from src.core.tools.identity_binding import get_binding_code_store

    from .identity import _resolve_main_model

    code_store = get_binding_code_store()
    code = await code_store.generate(
        actor_id=actor_id, space_id=space_id, display_name=current_speaker,
    )
    main_model = await _resolve_main_model(http_request)
    return _build_bind_response(
        f"跨平台绑定验证码: {code}\n请在另一端发送「{bind_prefix} {code}」完成绑定。验证码 5 分钟内有效。",
        model=main_model or VIRTUAL_MODEL_ANY,
    )


async def _bind_confirm(
    http_request: Request,
    input_code: str,
    actor_id: str,
) -> JSONResponse:
    """确认绑定: 校验验证码并执行绑定."""
    from src.core.tools.identity_binding import get_binding_code_store

    code_store = get_binding_code_store()
    entry = await code_store.verify(input_code)
    if entry is None:
        return _build_bind_response("验证码无效或已过期。")

    identity_store = _get_identity_store(http_request)
    if not identity_store:
        return _build_bind_response("身份存储不可用, 绑定失败。")

    target_actor_id = entry["actor_id"]
    target_groups = await identity_store.list_actor_groups(target_actor_id)
    current_groups = await identity_store.list_actor_groups(actor_id)

    if current_groups:
        msg = "当前账号已绑定, 无法重复绑定。"
    elif target_groups:
        group_id = target_groups[0].id
        await identity_store.bind_actor_to_group(actor_id, group_id)
        migrated = await _migrate_relationships(http_request, actor_id, group_id)
        if migrated:
            logger.info("relationship migration: actor=%s -> group=%s, %d rows", actor_id, group_id, migrated)
        msg = f"绑定成功! 已加入用户组 {group_id}。"
    else:
        group = await identity_store.create_group(name=None)
        await identity_store.bind_actor_to_group(target_actor_id, group.id)
        await identity_store.bind_actor_to_group(actor_id, group.id)
        for aid in (target_actor_id, actor_id):
            migrated = await _migrate_relationships(http_request, aid, group.id)
            if migrated:
                logger.info("relationship migration: actor=%s -> group=%s, %d rows", aid, group.id, migrated)
        msg = f"绑定成功! 已创建新用户组 {group.id}。"

    return _build_bind_response(msg)


async def _migrate_relationships(http_request: Request, actor_id: str, group_id: str) -> int:
    """迁移 actor 的既有关系到 group."""
    from src.api.deps import _state_or_none
    from src.core.constants import DEFAULT_PERSONA_ID

    state = _state_or_none(http_request)
    relationship_store = state.relationship_store if state else None
    if not relationship_store:
        return 0
    return await relationship_store.migrate_relationships_to_group(
        DEFAULT_PERSONA_ID, actor_id, group_id,
    )


def _build_bind_response(content: str, *, model: str = VIRTUAL_MODEL_ANY) -> JSONResponse:
    """构建身份绑定 JSONResponse."""
    from fastapi.responses import JSONResponse

    return JSONResponse(content={
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


# ── 初始状态构建 ──────────────────────────────────────────────


def _build_initial_state(
    *,
    messages_dict: list[dict[str, Any]],
    allowed_tools: list[Any] | None,
    request: ChatCompletionRequest,
    tool_transaction: Any,
    tool_policy: ToolPolicy | None,
    expression_style: str,
    interaction_id: str | None,
    internal_tool_names: set[str],
    source_user: str | None,
    current_speaker: str,
    actor_id: str | None,
    persona: str,
    persona_name: str,
    persona_definition: Any,
    use_proxy: bool,
    main_model: str,
    source_frontend: str | None,
    space_id: str | None,
    channel_type: str | None,
    external_event_id: str | None,
    api_key_id: str | None,
    preprocess_result: Any,
    prompt_cleaning_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """构建图执行所需的 initial_state 字典."""
    from src.core.constants import DEFAULT_PERSONA_ID as _PID

    state: dict[str, Any] = {
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
        "persona_id": _PID,
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
        state["prompt_cleaning_result"] = prompt_cleaning_result
    return state
