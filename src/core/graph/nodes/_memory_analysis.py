"""Memory analysis node: ReAct agent that extracts candidate memories."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.core.agents import run_memory_analysis
from src.core.agents.spec import get_spec
from src.core.agents.tracking import run_agent_tracked
from src.core.config import get_settings
from src.core.graph.state import AgentState
from src.core.memory import MemoryLifecycle
from src.core.models.resolver import RoleResolver
from src.infra.forwarder.multi import MultiForwarder
from src.infra.vector_store import VectorStore
from src.persistence.memory_store import SqliteMemoryStore
from src.persistence.notification_store import NotificationStore
from src.tools import MemoryRetriever, make_vector_search_tool

from ._helpers import _format_emotion_text, _resolve_addressing, _retrieval_context

logger = logging.getLogger(__name__)


async def memory_analysis_node(
    state: AgentState, config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Memory analysis agent: ReAct, extracts candidate memories. Decay handled by deterministic formula."""
    from src.core.graph.nodes import _get_stores

    if state.get("finish_reason") == "tool_calls":
        logger.debug("🧠 [memory_analysis] 工具中间轮, 跳过")
        return {"new_memories": [], "decay_evaluations": []}

    settings = get_settings()
    source_user = state["source_user"]
    # Non-attribution mode: no valid user, don't write any private memories
    if not source_user:
        logger.debug("🧠 [memory_analysis] 非归属模式, 跳过")
        return {"new_memories": [], "decay_evaluations": []}

    stores = _get_stores(config)
    forwarder: MultiForwarder = stores["multi_forwarder"]
    resolver: RoleResolver = stores["resolver"]
    memory_store: SqliteMemoryStore = stores["memory_store"]
    vector_store: VectorStore = stores["vector_store"]
    notification_store: NotificationStore = stores["notification_store"]
    owns_fwd = stores.get("_owns_forwarder", False)

    logger.debug("=" * 60)
    logger.debug("🧠 [memory_analysis] 开始处理")

    try:
        retriever = MemoryRetriever(forwarder, vector_store, memory_store)

        extracted = state.get("extracted_new", [])
        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in extracted
        )
        if not conversation.strip():
            logger.debug("  ⚠️ 无对话内容, 跳过")
            return {"new_memories": [], "decay_evaluations": []}

        rel_for_addressing = await stores["relationship_store"].get_relationship(state["persona_id"], source_user)
        persona_addr, user_addr, rel_ctx = _resolve_addressing(rel_for_addressing, settings)

        # Audience context: memory analysis agent dedup retrieval also filtered by current session audience
        tools = [
            make_vector_search_tool(retriever, _retrieval_context(state, rel_for_addressing)),
        ]

        # Get pre-computed emotion analysis from state
        emotion_analysis = state.get("emotion_analysis", {})
        emotion_text = _format_emotion_text(emotion_analysis)

        logger.debug("  🚀 调用记忆分析 Agent...")
        out = await run_agent_tracked(
            "memory_analysis",
            run_memory_analysis(
                forwarder=forwarder,
                source_user=source_user,
                conversation=conversation,
                tools=tools,
                max_iterations=get_spec("memory_analysis").max_iterations,
                persona_name=settings.persona.name,
                persona_addressing=persona_addr,
                user_addressing=user_addr,
                relation_context=rel_ctx,
                emotion_analysis=emotion_text,
                current_speaker=state.get("current_speaker") or "未知参与者",
                channel_type=state.get("channel_type"),
            ),
            store=stores.get("agent_run_store"),
            debug_bus=stores.get("debug_bus"),
            parent_request_id=state.get("interaction_id"),
        )

        logger.debug("  ✅ 记忆分析完成: 新记忆 %d 条", len(out.new_memories))

        lifecycle = MemoryLifecycle(memory_store, vector_store, forwarder, resolver=resolver)
        lifecycle.notification_store = notification_store
        for cand in out.new_memories:
            await lifecycle.store_candidate(
                cand, source_user=source_user, space_id=state.get("space_id"),
            )

        # Deterministic decay: formula batch-update all NORMAL memories
        await lifecycle.run_deterministic_decay()

        return {
            "new_memories": [
                {
                    "content": m.content, "memory_type": m.memory_type.value,
                    "importance": m.importance, "reasoning": m.reasoning,
                }
                for m in out.new_memories
            ],
            "decay_evaluations": [],
        }
    except Exception as e:
        logger.error("记忆分析失败: %s", e)
        return {"errors": [f"memory_analysis: {e}"]}
    finally:
        if owns_fwd:
            await forwarder.close()
