"""Main dialogue node and its pipeline helpers.

Pipeline stages:
1. _prepare_context  -- load relationships/memories/emotion, assemble LLM messages
2. _invoke_llm       -- call LLM + internal tool interception
3. _process_response -- extract text + Expressor rewrite
4. _extract_metadata -- assemble return dict
"""

import logging
from typing import TYPE_CHECKING, Any

import aiosqlite
from langchain_core.runnables import RunnableConfig

from src.core.agents import run_main_dialogue
from src.core.config import get_settings
from src.core.graph.state import AgentState

if TYPE_CHECKING:
    from src.core.config import Settings
from src.core.memory import build_main_dialogue_messages, format_relationship
from src.core.memory.trigger_reason import infer_trigger_reason
from src.core.utils import last_user_message
from src.infra.forwarder.multi import MultiForwarder
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore
from src.tools import MemoryRetriever

from ._helpers import StoresDict, _compute_emotion, _retrieval_context

logger = logging.getLogger(__name__)


async def _prepare_context(
    state: AgentState,
    config: RunnableConfig | None,
    settings: "Settings",
    forwarder: MultiForwarder,
    memory_store: SqliteMemoryStore,
    vector_store: VectorStore,
    stores: StoresDict,
) -> dict[str, Any]:
    """Load relationships, permanent/retrieved memories, emotion analysis, assemble LLM messages.

    Returns:
        dict with ``rel``, ``messages``, ``emotion_analysis`` for downstream pipeline stages.
    """
    from src.core.memory.audience import AudienceFilter

    source_user = state["source_user"]

    rel = await stores["relationship_store"].get_relationship(
        state["persona_id"], source_user,
    ) if source_user else None
    logger.debug("  💝 关系状态: %s", format_relationship(rel) if rel else "(无)")
    retrieval_ctx = _retrieval_context(state, rel)

    perms = await memory_store.list_permanent(
        source_user or None,
        limit=settings.memory.permanent_load_top,
        space_id=state.get("space_id"),
    )
    perms = AudienceFilter.filter(perms, retrieval_ctx)
    logger.debug("  📚 永久记忆: %d 条", len(perms))
    for p in perms:
        await memory_store.mark_accessed(p.id)

    extracted = state.get("extracted_new", [])
    query = last_user_message(extracted)

    logger.debug("  🔍 检索查询: %s", query[:100] if query else "(空)")

    retrieved_entries: list[Any] = []
    if query:
        retriever = MemoryRetriever(forwarder, vector_store, memory_store)
        results = await retriever.search(
            query, top_k=settings.memory.retrieval_top_k,
            retrieval_ctx=retrieval_ctx,
        )
        logger.debug("  🔍 检索结果: %d 条", len(results))
        for r in results:
            await memory_store.mark_accessed(r.memory_id)
            entry = await memory_store.get_by_id(r.memory_id)
            if entry:
                retrieved_entries.append(entry)

    # Emotion analysis: pre-compute once, shared by memory_analysis + relationship_analysis
    emotion_analysis = await _compute_emotion(forwarder, extracted)
    logger.debug("  💭 情绪分析: %s (强度=%.2f)", emotion_analysis.get("emotion", "?"), emotion_analysis.get("intensity", 0))

    conversation_history = state.get("messages", [])
    conversation_history = [m for m in conversation_history if m.get("role") != "system"]

    # Infer trigger reason (no client changes needed)
    extracted_user = last_user_message(extracted)
    trigger = infer_trigger_reason(
        state.get("current_speaker"),
        extracted_user,
        channel_type=state.get("channel_type"),
    )

    # Debug event: trigger reason
    from src.infra.debug_context import emit_pipeline
    emit_pipeline(
        (config or {}).get("configurable", {}).get("debug_bus") if config else None,
        event_kind="trigger_reason",
        reason=trigger,
        channel_type=state.get("channel_type"),
    )

    # Load Lorebook entries (keyword matching)
    lorebook_entries: list[Any] = []
    lorebook_store = stores.get("lorebook_store")
    if lorebook_store is not None and query:
        try:
            lorebook_entries = await lorebook_store.match_for_space(
                query, space_id=state.get("space_id"), limit=5,
            )
        except aiosqlite.Error:
            logger.warning("Lorebook match failed", exc_info=True)

    messages = build_main_dialogue_messages(
        persona_prompt=state.get("persona") or settings.persona.prompt,
        persona_name=state.get("persona_name") or settings.persona.name,
        user_name=state.get("current_speaker") or "未知参与者",
        permanent_memories=perms,
        retrieved_memories=retrieved_entries,
        relationship=rel,
        conversation_history=conversation_history,
        proxy_thinking_result=state.get("proxy_thinking_result"),
        current_speaker=state.get("current_speaker"),
        channel_type=state.get("channel_type"),
        space_label=state.get("space_id"),
        active_participants=state.get("active_participants"),
        trigger_reason=trigger,
        tools=state.get("tools"),
        persona_definition=state.get("persona_definition"),
        space_id=state.get("space_id"),
        lorebook_entries=lorebook_entries,
    )

    logger.debug("  📝 拼装消息数: %d", len(messages))

    return {
        "rel": rel,
        "messages": messages,
        "emotion_analysis": emotion_analysis,
    }


async def _invoke_llm(
    forwarder: MultiForwarder,
    messages: list[Any],
    state: AgentState,
    stores: StoresDict,
) -> Any:
    """Call LLM to generate reply, with internal tool interception + second-pass logic."""
    logger.debug("  🚀 调用 LLM 生成回复...")
    dialogue = await run_main_dialogue(
        forwarder,
        messages,
        tools=state.get("tools"),
        tool_choice=state.get("tool_choice"),
        parallel_tool_calls=state.get("parallel_tool_calls"),
    )

    # Internal tool interception: when model calls an internal tool, execute server-side, then call LLM again
    internal_names: set[str] = state.get("internal_tool_names") or set()
    if dialogue.finish_reason == "tool_calls" and internal_names:
        import json as _json

        from src.core.tools.internal_registry import get_internal_tool_registry

        registry = get_internal_tool_registry()
        tool_calls = dialogue.message.get("tool_calls") or []
        internal_calls = [
            tc for tc in tool_calls
            if tc.get("function", {}).get("name") in internal_names
        ]
        client_calls = [
            tc for tc in tool_calls
            if tc.get("function", {}).get("name") not in internal_names
        ]

        if internal_calls:
            logger.debug("  🔧 内部 tool 拦截: %d 个", len(internal_calls))
            # Execute internal tools, build tool_result
            messages_with_tools = list(messages) + [dict(dialogue.message)]
            identity_store = stores.get("identity_store")
            for tc in internal_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                handler_tool = registry.get(tool_name)
                if handler_tool is None:
                    continue
                # Parse arguments
                try:
                    args = _json.loads(func.get("arguments") or "{}")
                except _json.JSONDecodeError:
                    args = {}
                # Execute handler
                try:
                    result = await handler_tool.handler(
                        actor_id=state.get("actor_id"),
                        space_id=state.get("space_id"),
                        display_name=state.get("current_speaker"),
                        identity_store=identity_store,
                        **args,
                    )
                # 内部 tool handler 是任意业务代码, 其异常需转成 model 可见的
                # tool_result, 不能中断对话. 保留裸捕获兜底.
                except Exception as e:
                    result = {"success": False, "error": str(e)}
                messages_with_tools.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": _json.dumps(result, ensure_ascii=False),
                })
                logger.debug("  🔧 内部 tool %s 结果: %s", tool_name, result)

            # Second LLM call for natural reply based on tool_result
            logger.debug("  🚀 内部 tool 执行完毕, 再调 LLM...")
            dialogue = await run_main_dialogue(
                forwarder,
                messages_with_tools,
                tools=state.get("tools"),
                tool_choice=None,  # Second round: no forced tools
                parallel_tool_calls=state.get("parallel_tool_calls"),
            )
            # If second round has no internal tool_calls, stop interception (prevent infinite loop)
            # Merge first round client tool_calls (if any)
            second_tool_calls = dialogue.message.get("tool_calls") or []
            if client_calls and not second_tool_calls:
                dialogue.message["tool_calls"] = client_calls
                dialogue.finish_reason = "tool_calls"

    return dialogue


async def _process_response(
    dialogue: Any,
    state: AgentState,
    forwarder: MultiForwarder,
    config: RunnableConfig | None,
    stores: StoresDict,
    rel: Any,
) -> str:
    """Extract reply text; in group-chat mode, rewrite via Expressor."""
    content = dialogue.message.get("content")
    response = content if isinstance(content, str) else ""

    # Expressor rewrite (group-chat final text only, not tool calls)
    if (
        dialogue.finish_reason == "stop"
        and response
        and state.get("channel_type") == "group"
    ):
        from src.core.agents import ExpressorConfig, run_expressor

        # Load space social policy from space_policy_store
        space_policy = None
        space_policy_store = stores.get("space_policy_store")
        space_id = state.get("space_id")
        if space_policy_store is not None and space_id:
            try:
                space_policy = await space_policy_store.get(space_id)
            except aiosqlite.Error:
                logger.warning("Failed to load space policy for expressor", exc_info=True)

        expressor_cfg = ExpressorConfig(
            enabled=space_policy.expressor_enabled if (space_policy and space_policy.expressor_enabled is not None) else True,
            temperature=space_policy.expressor_temperature if space_policy is not None else 0.4,
        )
        relationship_summary = format_relationship(rel)
        expression_style = state.get("expression_style", "")
        rewritten = await run_expressor(
            forwarder,
            response,
            state.get("current_speaker") or "未知参与者",
            state.get("channel_type"),
            relationship_summary,
            config=expressor_cfg,
            expression_style=expression_style,
        )
        if rewritten != response:
            logger.debug(
                "  ✨ Expressor 改写: %d → %d",
                len(response), len(rewritten),
            )
            # Debug event: Expressor rewrite comparison
            from src.infra.debug_context import emit_pipeline
            emit_pipeline(
                (config or {}).get("configurable", {}).get("debug_bus") if config else None,
                event_kind="expressor_rewrite",
                original_length=len(response),
                rewritten_length=len(rewritten),
                original_preview=response[:200],
                rewritten_preview=rewritten[:200],
                expression_style=expression_style or None,
            )
            response = rewritten
            dialogue.message["content"] = rewritten

    return response


def _extract_metadata(
    response: str,
    dialogue: Any,
    emotion_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Assemble final return dict from dialogue result."""
    logger.debug(
        "  ✅ 生成完成, 长度: %d, finish_reason: %s",
        len(response),
        dialogue.finish_reason,
    )
    result: dict[str, Any] = {
        "response": response,
        "response_message": dialogue.message,
        "finish_reason": dialogue.finish_reason,
        "emotion_analysis": emotion_analysis,
    }
    if dialogue.usage:
        result["upstream_usage"] = dialogue.usage
    return result


async def main_dialogue_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Main dialogue agent: load memory + assemble context + generate reply.

    Pipeline consists of four stages:
    1. ``_prepare_context``  -- load relationships/memories/emotion, assemble LLM messages
    2. ``_invoke_llm``       -- call LLM + internal tool interception
    3. ``_process_response`` -- extract text + Expressor rewrite
    4. ``_extract_metadata`` -- assemble return dict
    """
    from src.core.graph.nodes import _get_stores

    # In streaming mode _run_memory_graph has pre-filled response: return directly, don't call LLM again
    if "response" in state and state["response"] is not None:
        logger.debug("=" * 60)
        logger.debug("🤖 [main_dialogue] 检测到预填充 response: 跳过 LLM 调用")
        result: dict[str, Any] = {"response": state["response"]}
        if "upstream_usage" in state and state["upstream_usage"] is not None:
            result["upstream_usage"] = state["upstream_usage"]
        return result

    settings = get_settings()
    stores = _get_stores(config)
    forwarder: MultiForwarder = stores["multi_forwarder"]
    memory_store: SqliteMemoryStore = stores["memory_store"]
    vector_store: VectorStore = stores["vector_store"]
    owns_fwd = stores.get("_owns_forwarder", False)

    logger.debug("=" * 60)
    logger.debug("🤖 [main_dialogue] 开始处理")
    logger.debug("  source_user: %s", state["source_user"])

    try:
        ctx = await _prepare_context(
            state, config, settings, forwarder, memory_store, vector_store, stores,
        )
        dialogue = await _invoke_llm(forwarder, ctx["messages"], state, stores)
        response = await _process_response(
            dialogue, state, forwarder, config, stores, ctx["rel"],
        )
        return _extract_metadata(response, dialogue, ctx["emotion_analysis"])
    finally:
        if owns_fwd:
            await forwarder.close()
