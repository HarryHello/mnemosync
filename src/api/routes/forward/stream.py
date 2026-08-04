"""流式处理: 加载记忆 → 代理推理 → 转发上游 → 后台记忆图."""
import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from src.api.deps import _state
from src.api.reasoning_control import (
    build_reasoning_stream_frames,
    chunk_has_native_reasoning,
    mark_native_reasoning,
)
from src.api.schemas.forward import ChatCompletionRequest
from src.api.tool_policies import filter_tool_calls, validate_tool_arguments
from src.api.tool_transactions import append_tool_transaction_context
from src.core.agents import run_proxy_thinking
from src.core.agents.tracking import run_agent_tracked
from src.core.config import Settings, get_settings
from src.core.constants import VIRTUAL_MODEL_ANY
from src.core.memory import MemoryEntry, Relationship, format_relationship
from src.persistence.conversation_store import SqliteConversationStore
from src.core.memory.context import (
    build_main_dialogue_messages,
    render_main_dialogue_system,
)
from src.core.memory.short_term import build_short_term_history, token_count_for_storage
from src.core.models.resolver import NoCandidateForRoleError
from src.core.utils import last_user_message
from src.infra.debug_context import emit_pipeline, use_agent
from src.infra.forwarder import StreamResult, UpstreamError, UpstreamTimeout, parse_sse_stream_full
from src.infra.forwarder.multi import MultiForwarder, UpstreamAllCandidatesFailed
from src.infra.llm_service.models import ModelType
from src.tools import MemoryRetriever

from ._accessors import _build_graph_config, _get_conversation_store, _get_multi_forwarder
from .idempotency import _record_idempotency
from .identity import _resolve_main_candidate
from .memory_graph import _run_memory_graph
from .persistence import _persist_assistant_event, _persist_plugin_events

logger = logging.getLogger(__name__)


# ── SSE delta helpers ────────────────────────────────────────────


@dataclass
class _StreamAssemblyResult:
    """Mutable container shared between ``_assemble_deltas`` and its caller.

    Because ``_assemble_deltas`` is an async generator it cannot return a
    value; instead the caller passes in a result object and reads it back
    after the generator is exhausted.
    """

    collected_chunks: list[bytes] = field(default_factory=list)
    saw_native: bool = False
    errored: bool = False


def _parse_sse_event(collected_chunks: list[bytes]) -> StreamResult:
    """Parse accumulated SSE byte-chunks into a structured ``StreamResult``.

    Thin wrapper around ``parse_sse_stream_full`` that gives the streaming
    pipeline a single, named call-site for the parsing concern.
    """
    return parse_sse_stream_full(collected_chunks)


def _handle_stream_errors(exc: Exception) -> bytes:
    """Format an upstream error as an SSE ``data:`` frame.

    Returns the encoded frame so the caller can ``yield`` it.  Unknown
    exception types are re-raised because they indicate a bug rather than
    an upstream failure.
    """
    if isinstance(exc, UpstreamTimeout):
        logger.debug("⏰ 流式超时: %s", exc)
        return f'data: {{"error": "{exc}"}}\n\n'.encode()
    if isinstance(exc, UpstreamError):
        logger.debug("❌ 流式错误: %s", exc.message)
        return f'data: {{"error": "{exc.message}"}}\n\n'.encode()
    if isinstance(exc, UpstreamAllCandidatesFailed):
        logger.debug("❌ 所有候选失败: %s", exc)
        return f'data: {{"error": "all candidates failed: {exc}"}}\n\n'.encode()
    if isinstance(exc, NoCandidateForRoleError):
        logger.debug("❌ 无候选: %s", exc)
        return f'data: {{"error": "no candidate: {exc}"}}\n\n'.encode()
    raise exc


async def _assemble_deltas(
    result: _StreamAssemblyResult,
    *,
    multi_forwarder: MultiForwarder,
    messages_with_memory: list[dict[str, Any]],
    temperature: float | None,
    max_tokens: int | None,
    passthrough: dict[str, Any],
    reasoning_text: str | None,
    chatcmpl_id: str,
    main_model: str,
) -> AsyncGenerator[bytes, None]:
    """Yield SSE frames: proxy-reasoning deltas followed by upstream content.

    Collects every raw upstream chunk in *result* so the caller can
    post-process them after the stream ends.  On upstream errors an SSE
    error frame is yielded and the generator stops (no exception escapes).
    """
    if reasoning_text:
        for frame in build_reasoning_stream_frames(
            reasoning_text, chatcmpl_id=chatcmpl_id, model=main_model,
        ):
            yield frame

    try:
        logger.debug("🚀 开始流式转发 (带记忆上下文)...")
        with use_agent("main_dialogue_stream"):
            async for chunk in multi_forwarder.chat_stream(
                ModelType.MAIN,
                messages=messages_with_memory,
                temperature=temperature,
                max_tokens=max_tokens,
                **passthrough,
            ):
                result.collected_chunks.append(chunk)
                if not result.saw_native and chunk_has_native_reasoning(chunk):
                    result.saw_native = True
                yield chunk
        logger.debug("✅ 流式转发完成, chunks: %d", len(result.collected_chunks))
    except (UpstreamTimeout, UpstreamError, UpstreamAllCandidatesFailed,
            NoCandidateForRoleError) as exc:
        result.errored = True
        yield _handle_stream_errors(exc)
        return

    if result.saw_native:
        mark_native_reasoning(main_model)


async def _dispatch_callbacks(
    http_request: Request,
    initial_state: dict[str, Any],
    *,
    chatcmpl_id: str,
    main_model: str,
    new_user_content: str,
    conversation_store: SqliteConversationStore,
    collected_chunks: list[bytes],
    reasoning_text: str | None,
    stream_result: StreamResult,
) -> None:
    """Post-stream: filter tool calls, persist events, record idempotency,
    and trigger the background memory graph.

    This runs after ``_assemble_deltas`` has finished yielding.  Errors in
    persistence are logged but never raised so the client always receives
    its response.
    """
    source_user = initial_state.get("source_user") or ""
    source_frontend = initial_state.get("source_frontend")
    actor_id = initial_state.get("actor_id")
    space_id = initial_state.get("space_id")
    external_event_id = initial_state.get("external_event_id")
    api_key_id = initial_state.get("api_key_id")

    assistant_text = stream_result.text or ""
    assistant_finish_reason = stream_result.finish_reason
    assistant_tool_calls = stream_result.tool_calls

    # Build response_message with outbound tool-call filtering for persistence.
    response_message: dict[str, Any] | None = None
    if assistant_tool_calls:
        valid_calls = assistant_tool_calls
        removed: list[str] = []
        policy = initial_state.get("tool_policy")
        tools = initial_state.get("tools")
        valid_calls, issues = validate_tool_arguments(valid_calls, tools)
        removed.extend(issues)
        if policy:
            valid_calls, pol_removed = filter_tool_calls(valid_calls, policy)
            removed.extend(pol_removed)
        if removed:
            logger.debug("  🔧 流式出站过滤 (持久化层): 移除 %s", removed)
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

    # Persist structured event stream.
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

    # Record idempotency cache for first successful response.
    await _record_idempotency(
        http_request, api_key_id, external_event_id, chatcmpl_id, assistant_text,
        response_message=response_message,
        finish_reason=assistant_finish_reason,
    )

    # NOTE: memory graph task is created OUTSIDE the generator
    # (in locked_stream) to avoid the finally block cancelling it immediately.


# ── Main stream entry point ─────────────────────────────────────


async def _load_memory_context(
    http_request: Request,
    initial_state: dict[str, Any],
    *,
    settings: Settings,
    multi_forwarder: MultiForwarder,
) -> tuple[Relationship | None, list[MemoryEntry], list[MemoryEntry], str]:
    """加载 permanent + retrieved 记忆, 并解析当前轮的唯一 user 输入.

    Returns ``(rel, perms, retrieved_entries, new_user_content)``. 客户端
    history 视为"不可信": 服务器有自己的跨前端流水。普通请求只取最后一条
    user 消息；工具续轮没有新的 user 输入，只接入已校验事务尾部。
    """
    source_user = initial_state.get("source_user") or ""
    space_id = initial_state.get("space_id")
    actor_id = initial_state.get("actor_id")

    logger.debug("🧠 加载记忆上下文...")
    _st = _state(http_request)
    memory_store = _st.memory_store
    vector_store = _st.vector_store
    relationship_store = _st.relationship_store
    assert memory_store is not None
    assert vector_store is not None
    assert relationship_store is not None

    from src.core.memory.audience import AudienceFilter, RetrievalContext

    rel = await relationship_store.get_relationship(
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

    client_messages = initial_state.get("messages", [])
    tool_transaction = initial_state.get("tool_transaction")
    new_user_content = ""
    if not tool_transaction:
        new_user_content = last_user_message(client_messages)

    retrieval_query = (
        tool_transaction.root_user_content if tool_transaction else new_user_content
    )
    retrieved_entries: list[MemoryEntry] = []
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

    return rel, perms, retrieved_entries, new_user_content


async def _run_proxy_thinking(
    http_request: Request,
    initial_state: dict[str, Any],
    *,
    use_proxy_thinking: bool,
    multi_forwarder: MultiForwarder,
    current_speaker: str,
    rel: Relationship | None,
    perms: list[MemoryEntry],
    new_user_content: str,
) -> str | None:
    """(可选) 代理推理, 与检索串行; 失败时退化为普通转发."""
    reasoning_text: str | None = None
    if use_proxy_thinking:
        logger.debug("🤔 [代理推理] 开始 (ASSIST role)")
        try:
            perms_text = "\n".join(f"- {e.content}" for e in perms) or "（无）"
            _st = getattr(http_request.app, "state", None)
            reasoning_text = await run_agent_tracked(
                "proxy_thinking",
                run_proxy_thinking(
                    forwarder=multi_forwarder,
                    user_name=current_speaker,
                    relationship=format_relationship(rel) if rel else "新用户",
                    memories=perms_text,
                    user_message=new_user_content,
                    tools=None,
                    channel_type=initial_state.get("channel_type"),
                ),
                store=getattr(_st, "agent_run_store", None) if _st else None,
                debug_bus=getattr(_st, "debug_bus", None) if _st else None,
                parent_request_id=initial_state.get("interaction_id"),
            )
            logger.debug("  ✅ 代理推理完成, 长度: %d", len(reasoning_text) if reasoning_text else 0)
        except Exception as e:
            logger.warning("代理推理失败, 退化为普通转发: %s", e)
            reasoning_text = None
    return reasoning_text


async def _build_stream_messages(
    initial_state: dict[str, Any],
    request: ChatCompletionRequest,
    *,
    settings: Settings,
    main_ctx_length: int | None,
    conversation_store: SqliteConversationStore,
    current_speaker: str,
    rel: Relationship | None,
    perms: list[MemoryEntry],
    retrieved_entries: list[MemoryEntry],
    reasoning_text: str | None,
    new_user_content: str,
    space_id: str | None,
    source_user: str,
) -> list[dict[str, Any]]:
    """装填短期对话历史 + 拼装最终 messages (system + trimmed 历史 + 当前输入)."""
    persona = initial_state.get("persona") or settings.persona.prompt
    persona_name = initial_state.get("persona_name") or settings.persona.name
    tool_transaction = initial_state.get("tool_transaction")

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
        source_user=source_user or None,
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
        persona_definition=initial_state.get("persona_definition"),
        space_id=space_id,
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
    return messages_with_memory


def _make_streaming_response(
    http_request: Request,
    initial_state: dict[str, Any],
    request: ChatCompletionRequest,
    *,
    main_model: str,
    multi_forwarder: MultiForwarder,
    conversation_store: SqliteConversationStore,
    messages_with_memory: list[dict[str, Any]],
    new_user_content: str,
    reasoning_text: str | None,
) -> StreamingResponse:
    """构造 SSE 流式响应: stream_generator 转发 + locked_stream 后处理.

    后台记忆图任务在此创建 (``locked_stream``), 避免 finally 块立即取消它;
    空间锁也在流结束后释放。
    """
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

    # Shared container: generator populates, locked_stream reads after it finishes.
    _gen_result: dict[str, Any] = {}

    async def stream_generator() -> AsyncGenerator[bytes, None]:
        result = _StreamAssemblyResult()

        # Phase 1-2: Assemble deltas (reasoning frames + upstream content).
        async for chunk in _assemble_deltas(
            result,
            multi_forwarder=multi_forwarder,
            messages_with_memory=messages_with_memory,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            passthrough=passthrough,
            reasoning_text=reasoning_text,
            chatcmpl_id=chatcmpl_id,
            main_model=main_model,
        ):
            yield chunk

        # Early exit on error (error frame already yielded).
        if result.errored:
            return

        # Phase 3: Parse SSE chunks into structured result.
        stream_result = _parse_sse_event(result.collected_chunks)

        # Phase 4: Dispatch post-stream callbacks (persistence only, no bg task).
        await _dispatch_callbacks(
            http_request,
            initial_state,
            chatcmpl_id=chatcmpl_id,
            main_model=main_model,
            new_user_content=new_user_content,
            conversation_store=conversation_store,
            collected_chunks=result.collected_chunks,
            reasoning_text=reasoning_text,
            stream_result=stream_result,
        )

        # Pass data out for memory graph task creation (after generator exits).
        _gen_result["collected_chunks"] = result.collected_chunks
        _gen_result["assistant_finish_reason"] = stream_result.finish_reason
        _gen_result["assistant_text"] = stream_result.text or ""

    async def _trigger_memory_graph() -> asyncio.Task[Any] | None:
        """Create background memory graph task (called after stream ends).

        Returns the task so the caller can wait for it, or None if skipped.
        """
        collected_chunks = _gen_result.get("collected_chunks")
        if not collected_chunks:
            return None
        finish_reason = _gen_result.get("assistant_finish_reason")
        assistant_text = _gen_result.get("assistant_text", "")

        initial_state["proxy_thinking_enabled"] = False
        if reasoning_text:
            initial_state["proxy_thinking_result"] = reasoning_text

        # Skip memory graph for tool-only intermediate rounds.
        if finish_reason == "tool_calls" and not assistant_text:
            logger.debug("  ⏭️ 工具中间轮, 跳过记忆图")
            return None

        logger.debug("🔄 触发后台记忆图...")
        graph_config = _build_graph_config(http_request)
        task_key = f"memory_graph-{chatcmpl_id}"
        task = asyncio.create_task(
            _run_memory_graph(initial_state, collected_chunks, graph_config),
            name=task_key,
        )
        bg_tasks: dict[str, asyncio.Task[Any]] = getattr(http_request.app.state, "active_bg_tasks", {})
        if bg_tasks is not None:
            bg_tasks[task_key] = task

            def _on_done(t: asyncio.Task[Any], _key: str = task_key) -> None:
                bg_tasks.pop(_key, None)
                status = "ok"
                err_msg: str | None = None
                if t.cancelled():
                    status = "cancelled"
                else:
                    try:
                        err = t.exception()
                        if err is not None:
                            status = "failed"
                            err_msg = f"{type(err).__name__}: {err}"
                            logger.warning("后台记忆图任务失败: %s", err_msg)
                    except asyncio.CancelledError:
                        status = "cancelled"
                emit_pipeline(
                    getattr(http_request.app.state, "debug_bus", None),
                    event_kind="bg_task_done",
                    task_name=_key,
                    status=status,
                    error=err_msg,
                )

            task.add_done_callback(_on_done)
        return task

    # 包装: 流结束后触发记忆图 + 等待完成 + 释放空间锁
    async def locked_stream() -> AsyncGenerator[bytes, None]:
        bg_task: asyncio.Task[Any] | None = None
        try:
            async for chunk in stream_generator():
                yield chunk
            # Stream completed normally — trigger memory graph AFTER generator exits.
            bg_task = await _trigger_memory_graph()
        except Exception:
            logger.warning("流式响应异常", exc_info=True)
        finally:
            if bg_task is not None and not bg_task.done():
                # Memory graph still running — wait for it to finish.
                try:
                    await asyncio.wait_for(asyncio.shield(bg_task), timeout=120)
                except (TimeoutError, asyncio.CancelledError):
                    bg_task.cancel()
                    logger.debug("⏹️ 记忆图任务超时/取消: %s", chatcmpl_id)

            lock = initial_state.get("_space_lock")
            if lock is not None and lock.locked():
                lock.release()
                logger.debug("🔓 释放空间锁 (stream): %s", initial_state.get("_space_lock_key"))

    return StreamingResponse(
        locked_stream(),
        media_type="text/event-stream",
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
    current_speaker = initial_state.get("current_speaker") or "未知参与者"
    space_id = initial_state.get("space_id")
    main_candidate = await _resolve_main_candidate(
        http_request,
        require_tools=bool(initial_state.get("tools")),
        streaming=True,
    )
    main_model = main_candidate.model if main_candidate else VIRTUAL_MODEL_ANY
    main_ctx_length = main_candidate.context_length if main_candidate else None
    multi_forwarder = _get_multi_forwarder(http_request)
    conversation_store = _get_conversation_store(http_request)

    # 1. 加载记忆上下文 (permanent + retrieved) 并解析本轮 user 输入.
    rel, perms, retrieved_entries, new_user_content = await _load_memory_context(
        http_request,
        initial_state,
        settings=settings,
        multi_forwarder=multi_forwarder,
    )

    # 2. (可选) 代理推理.
    reasoning_text = await _run_proxy_thinking(
        http_request,
        initial_state,
        use_proxy_thinking=use_proxy_thinking,
        multi_forwarder=multi_forwarder,
        current_speaker=current_speaker,
        rel=rel,
        perms=perms,
        new_user_content=new_user_content,
    )

    # 3. 装填短期历史 + 拼装 messages.
    messages_with_memory = await _build_stream_messages(
        initial_state,
        request,
        settings=settings,
        main_ctx_length=main_ctx_length,
        conversation_store=conversation_store,
        current_speaker=current_speaker,
        rel=rel,
        perms=perms,
        retrieved_entries=retrieved_entries,
        reasoning_text=reasoning_text,
        new_user_content=new_user_content,
        space_id=space_id,
        source_user=source_user,
    )

    # 4. 构造 SSE 流式响应.
    return _make_streaming_response(
        http_request,
        initial_state,
        request,
        main_model=main_model,
        multi_forwarder=multi_forwarder,
        conversation_store=conversation_store,
        messages_with_memory=messages_with_memory,
        new_user_content=new_user_content,
        reasoning_text=reasoning_text,
    )
