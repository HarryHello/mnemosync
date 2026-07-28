"""非流式处理: 运行完整图, 返回结果."""
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.schemas.forward import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    UsageInfo,
)
from src.api.tool_policies import (
    check_persisted_cooldowns,
    filter_tool_calls,
    validate_tool_arguments,
)
from src.api.tool_transactions import append_tool_transaction_context
from src.core.config import get_settings
from src.core.memory.short_term import build_short_term_history, token_count_for_storage
from src.infra.debug_context import emit_pipeline

from . import _build_graph_config, _get_compiled_graph, _get_conversation_store
from .idempotency import _record_idempotency
from .identity import _resolve_main_candidate, _resolve_main_model
from .persistence import _persist_assistant_event, _persist_plugin_events

logger = logging.getLogger(__name__)


async def _handle_non_stream(
    http_request: Request,
    initial_state: dict[str, Any],
    request: "ChatCompletionRequest",
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

    main_candidate = await _resolve_main_candidate(
        http_request,
        require_tools=bool(initial_state.get("tools")),
        streaming=False,
    )
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
    graph_config = _build_graph_config(http_request)

    logger.debug("🚀 开始执行图 (非流式)...")
    try:
        final_state = await graph.ainvoke(initial_state, config=graph_config)
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
        # 策略过滤: 白名单/黑名单/内存冷却
        if policy:
            kept, policy_removed = filter_tool_calls(valid_calls, policy)
            removed_calls.extend(policy_removed)
            valid_calls = kept
        # 持久化冷却: 从 DB 查询最近的 tool_call 事件, 跨请求/重启生效
        if policy and policy.cooldown_seconds and valid_calls:
            kept, cooldown_violations = await check_persisted_cooldowns(
                conversation_store,
                valid_calls,
                policy,
                source_user=source_user or None,
                space_id=space_id,
            )
            removed_calls.extend(cooldown_violations)
            valid_calls = kept
        response_message = {**response_message, "tool_calls": valid_calls or None}
        if removed_calls:
            logger.debug("  🔧 出站过滤, 移除 %s", removed_calls)

        # 调试事件: 工具调用出站决策
        kept_names = [
            c.get("function", {}).get("name", "") for c in valid_calls
        ] if valid_calls else []
        emit_pipeline(
            getattr(http_request.app.state, "debug_bus", None),
            event_kind="tool_call_decision",
            stage="outbound",
            kept_calls=kept_names or None,
            removed_calls=removed_calls or None,
            finish_reason=finish_reason,
        )

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
