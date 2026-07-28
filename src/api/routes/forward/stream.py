"""流式处理: 加载记忆 → 代理推理 → 转发上游 → 后台记忆图."""
import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from src.api.reasoning_control import (
    build_reasoning_stream_frames,
    chunk_has_native_reasoning,
    mark_native_reasoning,
)
from src.api.schemas.forward import ChatCompletionRequest
from src.api.tool_policies import filter_tool_calls, validate_tool_arguments
from src.api.tool_transactions import append_tool_transaction_context
from src.core.agents import run_proxy_thinking
from src.core.config import get_settings
from src.core.constants import VIRTUAL_MODEL_ANY
from src.core.memory import format_relationship
from src.core.memory.context import (
    build_main_dialogue_messages,
    render_main_dialogue_system,
)
from src.core.memory.short_term import build_short_term_history, token_count_for_storage
from src.core.models.resolver import NoCandidateForRoleError
from src.infra.debug_context import emit_pipeline, use_agent
from src.infra.forwarder import UpstreamError, UpstreamTimeout, parse_sse_stream_full
from src.infra.forwarder.multi import UpstreamAllCandidatesFailed
from src.infra.llm_service.models import ModelType
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore
from src.tools import MemoryRetriever

from . import _build_graph_config, _get_conversation_store, _get_multi_forwarder
from .idempotency import _record_idempotency
from .identity import _resolve_main_candidate
from .memory_graph import _run_memory_graph
from .persistence import _persist_assistant_event, _persist_plugin_events

logger = logging.getLogger(__name__)


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
    main_candidate = await _resolve_main_candidate(
        http_request,
        require_tools=bool(initial_state.get("tools")),
        streaming=True,
    )
    main_model = main_candidate.model if main_candidate else VIRTUAL_MODEL_ANY
    main_ctx_length = main_candidate.context_length if main_candidate else None
    multi_forwarder = _get_multi_forwarder(http_request)
    conversation_store = _get_conversation_store(http_request)

    logger.debug("🧠 加载记忆上下文...")
    memory_store = SqliteMemoryStore(str(settings.storage.memory_db_abs))
    await memory_store.init_db()
    vector_store = VectorStore(str(settings.storage.chroma_dir_abs))

    from src.core.memory.audience import AudienceFilter, RetrievalContext

    rel = await memory_store.get_relationship(
        initial_state["persona_id"], source_user,
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

        # 构造 response_message 以支持 tool_calls 持久化; 流式路径出站过滤
        response_message: dict[str, Any] | None = None
        if assistant_tool_calls:
            valid_calls = assistant_tool_calls
            removed: list[str] = []
            policy = initial_state.get("tool_policy")
            tools = initial_state.get("tools")
            # 隐私检查 + 策略过滤 (SSE 帧已发出, 但确保持久化时过滤)
            valid_calls, issues = validate_tool_arguments(valid_calls, tools)
            removed.extend(issues)
            if policy:
                valid_calls, pol_removed = filter_tool_calls(valid_calls, policy)
                removed.extend(pol_removed)
            if removed:
                logger.debug("  🔧 流式出站过滤 (持久化层): 移除 %s", removed)
            # 调试事件: 工具调用出站决策 (流式)
            kept_names = [
                c.get("function", {}).get("name", "") for c in valid_calls
            ] if valid_calls else []
            emit_pipeline(
                getattr(http_request.app.state, "debug_bus", None),
                event_kind="tool_call_decision",
                stage="outbound_stream",
                kept_calls=kept_names or None,
                removed_calls=removed or None,
                finish_reason=assistant_finish_reason,
            )
            response_message = {
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": valid_calls or None,
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
            graph_config = _build_graph_config(http_request)
            asyncio.create_task(_run_memory_graph(initial_state, collected_chunks, graph_config))

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
    )
