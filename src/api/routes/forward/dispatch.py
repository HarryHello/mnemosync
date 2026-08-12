"""请求派发辅助: 模型验证、消息规范化、工具事务、提示词清洗、身份绑定、状态构建."""
import json as _json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

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


# ── 绑定上下文 ─────────────────────────────────────────────────


@dataclass
class BindContext:
    """绑定指令处理结果 — 由 _handle_identity_binding 返回.

    携带确定性的绑定结果数据和注入提示词, 由调用方构建精简 state 走 LLM 自然回复.
    """
    kind: Literal["initiate", "confirm"]
    success: bool
    code: str | None = None  # 发起绑定时的验证码
    message: str = ""  # 绑定结果描述 (注入到提示词)
    prompt_hint: str = ""  # 给模型的提示词


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
    """将请求消息转为 dict 列表.

    如果消息包含图片 content parts, 保留原始格式 (不展平),
    由后续的图片处理步骤根据模型能力决定是保留还是转述.
    如果消息不包含图片, 展平为纯文本 (保持旧行为).
    """
    result = [msg.model_dump(exclude_none=True) for msg in messages]
    for m in result:
        content = m.get("content")
        if isinstance(content, list):
            # 检查是否包含图片
            has_image = any(
                isinstance(p, dict) and p.get("type") == "image_url"
                for p in content
            )
            if has_image:
                # 保留原始 content parts, 由后续步骤处理
                pass
            else:
                # 纯文本 parts, 展平
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


async def process_images_in_messages(
    messages: list[dict[str, Any]],
    *,
    model_supports_images: bool,
    forwarder: Any = None,
    original_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """处理消息中的图片: 模型支持图片则从原始消息恢复, 否则调用 Vision Agent 转述.

    Args:
        messages: 规范化后的消息列表 (可能已被展平为纯文本)
        model_supports_images: 目标模型是否支持图片输入
        forwarder: MultiForwarder (模型不支持图片时需要)
        original_messages: 原始请求消息 (含完整 content parts)

    Returns:
        处理后的消息列表
    """
    from src.core.agents.vision import (
        describe_image,
        extract_image_parts,
        has_image_parts,
        strip_image_parts,
    )

    if model_supports_images and original_messages:
        # 模型支持图片, 从原始消息恢复 image parts
        # 构建原始消息的 role+content 索引
        orig_map: dict[tuple[str, str], dict[str, Any]] = {}
        for om in original_messages:
            role = om.get("role", "")
            # 用 content 的前100字符作为匹配键
            content_preview = str(om.get("content", ""))[:100]
            orig_map[(role, content_preview)] = om

        result = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            # 尝试从原始消息中找到对应的含图片版本
            if isinstance(content, str):
                content_preview = content[:100]
                orig = orig_map.get((role, content_preview))
                if orig and has_image_parts(orig.get("content")):
                    # 恢复原始 content parts
                    m = {**m, "content": orig["content"]}
            result.append(m)
        return result

    if not model_supports_images:
        # 模型不支持图片, 检查是否有图片需要转述
        needs_vision = any(has_image_parts(m.get("content")) for m in messages)
        if not needs_vision:
            return messages

        if forwarder is None:
            logger.warning("图片转述需要 forwarder, 降级为纯文本提取")
            for m in messages:
                if has_image_parts(m.get("content")):
                    m["content"] = strip_image_parts(m["content"])
            return messages

        # 调用 Vision Agent 转述图片
        result = []
        for m in messages:
            content = m.get("content")
            if has_image_parts(content):
                image_parts = extract_image_parts(content)
                text_part = strip_image_parts(content)
                descriptions = []
                for img in image_parts:
                    desc = await describe_image(forwarder, img)
                    descriptions.append(desc)
                combined = text_part
                if descriptions:
                    desc_text = "\n".join(f"[图片描述] {d}" for d in descriptions)
                    combined = f"{combined}\n{desc_text}" if combined else desc_text
                m = {**m, "content": combined.strip()}
            result.append(m)
        return result

    return messages


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
) -> BindContext | None:
    """处理身份绑定指令: 发起绑定 / 确认绑定.

    如果当前请求是绑定指令, 返回 BindContext (携带确定性结果 + 提示词);
    否则返回 None 继续正常流程.
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
) -> BindContext:
    """发起跨平台绑定: 生成验证码."""
    from src.core.tools.identity_binding import get_binding_code_store

    code_store = get_binding_code_store()
    code = await code_store.generate(
        actor_id=actor_id, space_id=space_id, display_name=current_speaker,
    )
    return BindContext(
        kind="initiate",
        success=True,
        code=code,
        message=f"验证码已生成: {code}",
        prompt_hint=(
            f"用户请求跨平台身份绑定。系统已生成验证码: {code}。"
            f"请用你的风格告诉用户验证码是 {code}，并在另一端发送「{bind_prefix} {code}」完成绑定。"
            f"验证码 5 分钟内有效。"
        ),
    )


async def _bind_confirm(
    http_request: Request,
    input_code: str,
    actor_id: str,
) -> BindContext:
    """确认绑定: 校验验证码并执行绑定."""
    from src.core.tools.identity_binding import get_binding_code_store

    code_store = get_binding_code_store()
    entry = await code_store.verify(input_code)
    if entry is None:
        return BindContext(
            kind="confirm",
            success=False,
            message="验证码无效或已过期",
            prompt_hint="用户尝试确认绑定，但验证码无效或已过期。请告知用户验证码不正确或已过期，需要重新发起绑定。",
        )

    identity_store = _get_identity_store(http_request)
    if not identity_store:
        return BindContext(
            kind="confirm",
            success=False,
            message="身份存储不可用",
            prompt_hint="绑定失败，身份存储不可用。请告知用户绑定暂时无法完成。",
        )

    target_actor_id = entry["actor_id"]
    if target_actor_id == actor_id:
        return BindContext(
            kind="confirm",
            success=False,
            message="不能与自身绑定",
            prompt_hint="用户尝试与自己绑定，这是不允许的。请告知用户不能与自身绑定。",
        )

    target_groups = await identity_store.list_actor_groups(target_actor_id)
    current_groups = await identity_store.list_actor_groups(actor_id)

    if current_groups:
        return BindContext(
            kind="confirm",
            success=False,
            message="当前账号已绑定, 无法重复绑定",
            prompt_hint="用户尝试绑定，但当前账号已经绑定过了。请告知用户已经绑定，无法重复绑定。",
        )
    elif target_groups:
        group_id = target_groups[0].id
        await identity_store.bind_actor_to_group(actor_id, group_id)
        migrated = await _migrate_relationships(http_request, actor_id, group_id)
        if migrated:
            logger.info("relationship migration: actor=%s -> group=%s, %d rows", actor_id, group_id, migrated)
        return BindContext(
            kind="confirm",
            success=True,
            message=f"绑定成功，已加入用户组 {group_id}",
            prompt_hint=f"用户成功完成了跨平台身份绑定，已加入用户组 {group_id}。请用你的风格告知用户绑定成功。",
        )
    else:
        group = await identity_store.create_group(name=None)
        await identity_store.bind_actor_to_group(target_actor_id, group.id)
        await identity_store.bind_actor_to_group(actor_id, group.id)
        for aid in (target_actor_id, actor_id):
            migrated = await _migrate_relationships(http_request, aid, group.id)
            if migrated:
                logger.info("relationship migration: actor=%s -> group=%s, %d rows", aid, group.id, migrated)
        return BindContext(
            kind="confirm",
            success=True,
            message=f"绑定成功，已创建新用户组 {group.id}",
            prompt_hint=f"用户成功完成了跨平台身份绑定，已创建新用户组 {group.id}。请用你的风格告知用户绑定成功。",
        )


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
